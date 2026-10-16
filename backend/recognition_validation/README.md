# Recognition validation dataset (data is not committed)

Use physically separate capture sessions. The canonical layout is:

```text
person001/
  gallery/session01/front/*.jpg
  gallery/session01/left/*.jpg
  gallery/session01/right/*.jpg
  probe/session02/front/*.jpg
  probe/session02/left/*.jpg
  probe/session02/right/*.jpg
  probe/session02/far/*.jpg
  probe/session02/low_light/*.jpg
```

For a held-out protocol, create separate roots
`recognition_validation/development/` and `recognition_validation/test/`, each
with the same person structure. Select candidate thresholds only on development;
pass an already approved candidate to test evaluation.

Optional `capture_metadata.csv` columns are `file,camera_id,capture_time`. Paths
are relative to the dataset root. Never commit student images or the manifest:
both contain biometric/personal data.
