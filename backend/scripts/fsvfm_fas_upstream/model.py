"""ViT-S/16 and FAS head used by the official cross_domain_FAS code."""

from __future__ import annotations

import math

import torch
from torch import nn


class PatchEmbed(nn.Module):
    def __init__(self, image_size=224, patch_size=16, embed_dim=384):
        super().__init__()
        self.img_size = (image_size, image_size)
        self.patch_size = (patch_size, patch_size)
        self.grid_size = (image_size // patch_size, image_size // patch_size)
        self.num_patches = self.grid_size[0] * self.grid_size[1]
        self.proj = nn.Conv2d(3, embed_dim, kernel_size=patch_size, stride=patch_size)

    def forward(self, value):
        if tuple(value.shape[-2:]) != self.img_size:
            raise ValueError(f"Expected {self.img_size} input, got {tuple(value.shape[-2:])}")
        return self.proj(value).flatten(2).transpose(1, 2)


class Mlp(nn.Module):
    def __init__(self, dim=384, hidden_dim=1536, drop=0.0):
        super().__init__()
        self.fc1 = nn.Linear(dim, hidden_dim)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_dim, dim)
        self.drop = nn.Dropout(drop)

    def forward(self, value):
        value = self.drop(self.act(self.fc1(value)))
        return self.drop(self.fc2(value))


class Attention(nn.Module):
    def __init__(self, dim=384, num_heads=12, qkv_bias=True, attn_drop=0.0, proj_drop=0.0):
        super().__init__()
        self.num_heads = num_heads
        self.scale = (dim // num_heads) ** -0.5
        self.qkv = nn.Linear(dim, dim * 3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_drop)
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_drop)

    def forward(self, value):
        batch, tokens, channels = value.shape
        qkv = self.qkv(value).reshape(batch, tokens, 3, self.num_heads, channels // self.num_heads)
        qkv = qkv.permute(2, 0, 3, 1, 4)
        query, key, val = qkv.unbind(0)
        attention = (query @ key.transpose(-2, -1)) * self.scale
        attention = self.attn_drop(attention.softmax(dim=-1))
        value = (attention @ val).transpose(1, 2).reshape(batch, tokens, channels)
        return self.proj_drop(self.proj(value))


class Block(nn.Module):
    def __init__(self, dim=384, num_heads=12, mlp_ratio=4.0):
        super().__init__()
        self.norm1 = nn.LayerNorm(dim, eps=1e-6)
        self.attn = Attention(dim, num_heads=num_heads, qkv_bias=True)
        self.drop_path = nn.Identity()  # official FAS CLI default drop_path=0
        self.norm2 = nn.LayerNorm(dim, eps=1e-6)
        self.mlp = Mlp(dim, int(dim * mlp_ratio))

    def forward(self, value):
        value = value + self.drop_path(self.attn(self.norm1(value)))
        return value + self.drop_path(self.mlp(self.norm2(value)))


class VisionTransformerSmall(nn.Module):
    """Official ViT-S/16: dim=384, depth=12, heads=12, global pooling."""

    def __init__(self):
        super().__init__()
        self.num_classes = 2
        self.num_features = self.embed_dim = 384
        self.num_tokens = 1
        self.patch_embed = PatchEmbed()
        self.cls_token = nn.Parameter(torch.zeros(1, 1, 384))
        self.pos_embed = nn.Parameter(torch.zeros(1, 197, 384))
        self.pos_drop = nn.Dropout(0.0)
        self.blocks = nn.Sequential(*[Block() for _ in range(12)])
        self.fc_norm = nn.LayerNorm(384, eps=1e-6)
        self.head = nn.Identity()
        nn.init.trunc_normal_(self.pos_embed, std=0.02)
        nn.init.trunc_normal_(self.cls_token, std=0.02)
        self.apply(self._init_weights)

    @staticmethod
    def _init_weights(module):
        if isinstance(module, nn.Linear):
            nn.init.trunc_normal_(module.weight, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.LayerNorm):
            nn.init.zeros_(module.bias)
            nn.init.ones_(module.weight)

    def forward_features(self, value):
        value = self.patch_embed(value)
        cls = self.cls_token.expand(value.shape[0], -1, -1)
        value = self.pos_drop(torch.cat((cls, value), dim=1) + self.pos_embed)
        value = self.blocks(value)
        pooled = self.fc_norm(value[:, 1:, :].mean(dim=1))
        result = torch.zeros_like(value)
        result[:, 0, :] = pooled
        result[:, 1:, :] = value[:, 1:, :]
        return result

    def forward(self, value):
        return self.head(self.forward_features(value))


def l2_norm(value, axis=1):
    return value / torch.norm(value, 2, axis, True)


class FeatureEmbedder(nn.Module):
    def __init__(self):
        super().__init__()
        self.bottleneck_layer_fc = nn.Linear(384, 512)
        self.bottleneck_layer_fc.weight.data.normal_(0, 0.005)
        self.bottleneck_layer_fc.bias.data.fill_(0.1)
        self.bottleneck_layer = nn.Sequential(
            self.bottleneck_layer_fc, nn.ReLU(), nn.Dropout(0.5)
        )

    def forward(self, value, norm_flag=True):
        value = self.bottleneck_layer(value)
        if norm_flag:
            norm = torch.norm(value, p=2, dim=1, keepdim=True).clamp(min=1e-12) ** 0.5 * math.sqrt(2)
            value = value / norm
        return value


class Classifier(nn.Module):
    def __init__(self):
        super().__init__()
        self.classifier_layer = nn.Linear(512, 2)
        self.classifier_layer.weight.data.normal_(0, 0.01)
        self.classifier_layer.bias.data.zero_()

    def forward(self, value, norm_flag=True):
        if norm_flag:
            with torch.no_grad():
                self.classifier_layer.weight.copy_(l2_norm(self.classifier_layer.weight, axis=0))
        return self.classifier_layer(value)


class FeatureGenerator(nn.Module):
    def __init__(self):
        super().__init__()
        self.vit = VisionTransformerSmall()

    def forward(self, value):
        return self.vit.forward_features(value)


class FSVFMFAS(nn.Module):
    """Official ``fas_model_fix`` with the ViT-S/16 variant."""

    def __init__(self):
        super().__init__()
        self.backbone = FeatureGenerator()
        self.embedder = FeatureEmbedder()
        self.classifier = Classifier()

    def forward(self, value, norm_flag=True):
        feature = self.embedder(self.backbone(value), norm_flag)
        return self.classifier(feature, norm_flag), feature

    def cls_logits(self, value):
        """Return the CLS-token logits consumed by official train/eval code."""
        logits, _ = self.forward(value, True)
        return logits[:, 0, :]


def build_vit_small_fas():
    return FSVFMFAS()
