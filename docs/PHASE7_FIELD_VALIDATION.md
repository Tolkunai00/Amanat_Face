# Phase 7 — field validation only

Дата проверки: 2026-10-09. Exact CLI help: [PHASE7_CLI_HELP.md](PHASE7_CLI_HELP.md).
Никакие production algorithms, thresholds, margin, attendance, enrollment,
PAD enforcement, FaceDecision или model weights не меняются. FS-VFM shadow-only.
Новые модели не загружаются. Все команды запускаются из backend.

## Фактическое начальное состояние

Оба существующих audit выполнены. Recognition и PAD: BLOCKED_DATASET, media=0.
Calibration и benchmark НЕ выполнены. Наличие трёх checkpoint файлов проверено;
это не подтверждает работоспособность providers или качество моделей.
Реальные camera sources, operator readiness и inventory devices пока не предоставлены.
Физический сбор требует оператора: программа начинает сохранять кадры сразу после запуска.

## 1. PowerShell: подготовка

```powershell
Set-Location 'C:\Users\user\PycharmProjects\Amanat_Face\backend'
$camera1Source = Read-Host 'Реальный источник camera1: индекс либо RTSP/HTTP'
if ([string]::IsNullOrWhiteSpace($camera1Source)) { throw 'Нужен реальный источник camera1' }
$camera2Source = Read-Host 'Реальный источник camera2; Enter, если отсутствует'
$fieldCameras = @(@{ Id = 'camera1'; Source = $camera1Source })
if (-not [string]::IsNullOrWhiteSpace($camera2Source)) {
    $fieldCameras += @{ Id = 'camera2'; Source = $camera2Source }
}
$fieldRun = Get-Date -Format 'yyyyMMdd_HHmmss'
$recognitionRoot = './recognition_validation/field_phase7'
$padRoot = './pad_validation/field_phase7'
$fieldReports = './pad_validation/field_phase7/reports'
```

camera1/camera2 — стабильные labels физических камер, не утверждение, что индексы 0/1 верны.
Источники вводятся оператором. Не сохраняйте URL с credentials в публичные отчёты.
Не запускайте capture параллельно с процессом, удерживающим ту же камеру.
Default preview показывает причины quality rejection; q/Esc завершает текущий capture.
30 кадров с интервалом 1 s — рабочий план, а не статистическая достаточность.
Считать фактически сохранённые кадры: ранний выход может оставить coverage gap.
Существующие capture quality gates не ослаблять; сохранять причины отказов для far/low_light.

## 2. Recognition: 10 лиц и заранее зафиксированный split

| Persons | Split | Gallery | Probe на каждой доступной камере |
|---|---|---|---|
| person01…person07 | development | front, left, right; camera1 | front, left, right, far, low_light |
| person08…person10 | test, held-out identities | front, left, right; camera1 | front, left, right, far, low_light |

Разделение 7/3 фиксируется ДО получения метрик; person08…person10 не участвуют
в выборе threshold/margin. Каждое лицо имеет собственную gallery session и
отдельную probe session на каждой камере. Gallery/probe — физически разные
сессии, а не только переименование ID. Перед probe человек выходит из зоны,
затем начинается новая съёмка; исходные gallery кадры не копировать.
На двух камерах все пять probe условий снимаются на обеих.
План: 10 gallery sessions и 20 probe sessions при двух камерах, либо 10 probe
sessions при одной. Фактические counts определяет audit.

```powershell
foreach ($personNumber in 1..10) {
    $person = 'person{0:D2}' -f $personNumber
    $split = if ($personNumber -le 7) { 'development' } else { 'test' }
    $recognitionOutput = "$recognitionRoot/$split"
    $gallerySession = "${fieldRun}_${person}_gallery_$([guid]::NewGuid().ToString('N'))"
    foreach ($condition in @('front', 'left', 'right')) {
        [void](Read-Host "Подготовьте $person GALLERY $condition camera1; Enter для capture")
        python -m scripts.capture_recognition_dataset --camera $camera1Source --camera-id camera1 --person-id $person --role gallery --session-id $gallerySession --condition $condition --output $recognitionOutput --sample-interval 1.0 --max-images 30
        if ($LASTEXITCODE -ne 0) { throw 'Capture gallery failed; остановить сбор и проверить причину' }
    }
    [void](Read-Host 'Завершите gallery, выйдите из зоны и подготовьте физически новую probe session; Enter')
    foreach ($camera in $fieldCameras) {
        $probeSession = "${fieldRun}_${person}_probe_$($camera.Id)_$([guid]::NewGuid().ToString('N'))"
        foreach ($condition in @('front', 'left', 'right', 'far', 'low_light')) {
            [void](Read-Host "Подготовьте $person PROBE $condition $($camera.Id); Enter для capture")
            python -m scripts.capture_recognition_dataset --camera $camera.Source --camera-id $camera.Id --person-id $person --role probe --session-id $probeSession --condition $condition --output $recognitionOutput --sample-interval 1.0 --max-images 30
            if ($LASTEXITCODE -ne 0) { throw 'Capture probe failed; остановить сбор и проверить причину' }
        }
    }
}
```

Повтор физически новой gallery/probe session получает новый ID. Не добавлять
новую попытку под старым session_id. Capture вызывает только camera/detection,
сохраняет изображения и metadata; не вызывает enrollment или attendance.

## 3. PAD: live и attacks

Минимум development на каждой доступной камере: пять live conditions и пять
attack types. Желательно все development persons, несколько физических devices
и несколько независимых попыток на каждый attack type/camera.
Held-out PAD собирается отдельно в test: новые attack sessions, новые source
identities и другие физические devices. Audit запрещает development/test
пересечение устройств и source identities. При отсутствии таких устройств
оставить test отсутствующим и PAD_VALIDATED=false.

LIVE development:

```powershell
foreach ($personNumber in 1..7) {
    $person = 'person{0:D2}' -f $personNumber
    foreach ($camera in $fieldCameras) {
        $liveSession = "${fieldRun}_${person}_live_$($camera.Id)_$([guid]::NewGuid().ToString('N'))"
        foreach ($condition in @('front', 'left', 'right', 'far', 'low_light')) {
            [void](Read-Host "Подготовьте LIVE $person $condition $($camera.Id); Enter для capture")
            python -m scripts.capture_pad_dataset --camera $camera.Source --camera-id $camera.Id --label live --person-id $person --session-id $liveSession --condition $condition --split development --output $padRoot --sample-interval 1.0 --max-images 30
            if ($LASTEXITCODE -ne 0) { throw 'PAD live capture failed' }
        }
    }
}
```

ATTACK development, одна независимая физическая попытка каждой категории на
каждой камере. Повторить блок для дополнительных устройств и попыток:

```powershell
foreach ($camera in $fieldCameras) {
    foreach ($attack in @('phone_photo', 'phone_video', 'laptop_photo', 'laptop_video', 'print')) {
        $sourceIdentity = Read-Host "Реальная source identity для $attack (например ID фактического участника)"
        $physicalDevice = Read-Host "Стабильный inventory ID реального display/print device для $attack"
        if ([string]::IsNullOrWhiteSpace($sourceIdentity) -or [string]::IsNullOrWhiteSpace($physicalDevice)) {
            throw 'Неизвестны обязательные metadata; не создавать фиктивную попытку'
        }
        $attackSession = "${fieldRun}_$($camera.Id)_${attack}_$([guid]::NewGuid().ToString('N'))"
        [void](Read-Host "Подготовьте НОВУЮ физическую попытку $attack $($camera.Id); Enter для capture")
        python -m scripts.capture_pad_dataset --camera $camera.Source --camera-id $camera.Id --label spoof --attack-type $attack --session-id $attackSession --source-identity $sourceIdentity --display-or-print-device $physicalDevice --split development --output $padRoot --sample-interval 1.0 --max-images 30
        if ($LASTEXITCODE -ne 0) { throw 'PAD attack capture failed' }
    }
}
```

CLI `--session-id` записывается как `attack_session_id` в metadata.json.
Одна физическая демонстрация replay video = один attack_session_id = одна
evidence group, независимо от числа sampled frames. Перезапуск replay как
отдельной попытки требует нового ID. Не делить кадры одного replay между splits.
Для test использовать те же live/attack команды с `--split test`, persons
person08…person10, новыми sessions и фактически отдельными source/devices.
Оператор проверяет отсутствие пересечений до capture.

Неизвестные optional metadata пропускать. Только известные факты можно добавить:
`--device-type`, `--screen-brightness`, `--paper-type`, `--print-size`, `--notes`.
Не заполнять brightness, paper и size предположениями. Print device ID должен
соответствовать реальному учёту, а notes может идентифицировать конкретный print.

## 4. Audit после collection: STOP при leakage

Recognition capture сохраняет отдельные CSV в output каждого split. Для общего
audit создаётся производный CSV с split prefixes; исходные CSV не изменяются:

```powershell
$combinedMetadata = foreach ($split in @('development', 'test')) {
    $metadataFile = "$recognitionRoot/$split/capture_metadata.csv"
    if (Test-Path -LiteralPath $metadataFile) {
        Import-Csv -LiteralPath $metadataFile | ForEach-Object {
            $_.file = "$split/$($_.file)"
            $_
        }
    }
}
if (-not $combinedMetadata) { throw 'STOP: нет recognition capture metadata' }
$combinedMetadata | Export-Csv -LiteralPath "$recognitionRoot/capture_metadata.csv" -NoTypeInformation -Encoding UTF8

python -m scripts.recognition_dataset_audit --dataset $recognitionRoot --metadata "$recognitionRoot/capture_metadata.csv" --manifest "$recognitionRoot/manifest_all.csv" --report "$recognitionRoot/audit_all.json"
$recognitionAuditExit = $LASTEXITCODE
python -m scripts.pad_dataset_audit --dataset $padRoot --report "$fieldReports/audit_all.json"
$padAuditExit = $LASTEXITCODE
$recognitionAudit = Get-Content -Raw "$recognitionRoot/audit_all.json" | ConvertFrom-Json
$padAudit = Get-Content -Raw "$fieldReports/audit_all.json" | ConvertFrom-Json
if ($recognitionAudit.protocol_violations.Count -gt 0 -or $padAudit.protocol_violations.Count -gt 0) {
    throw 'STOP: duplicate/leakage/protocol violation; исправить dataset и повторить audit, calibration запрещена'
}
if ($recognitionAuditExit -ne 0 -or -not $recognitionAudit.RECOGNITION_DATASET_READY) {
    throw 'STOP: recognition audit не READY'
}
if ($padAuditExit -ne 0 -or $padAudit.dataset_status -ne 'PASS' -or -not $padAudit.PAD_DATASET_COMPLETE) {
    throw 'STOP: PAD audit не PASS'
}

python -m scripts.recognition_dataset_audit --dataset "$recognitionRoot/development" --manifest "$recognitionRoot/manifest_development.csv" --report "$recognitionRoot/audit_development.json"
if ($LASTEXITCODE -ne 0) { throw 'STOP: recognition development audit failed' }
python -m scripts.pad_dataset_audit --dataset "$padRoot/development" --report "$fieldReports/audit_development.json"
if ($LASTEXITCODE -ne 0) { throw 'STOP: PAD development audit failed' }
```

Recognition проверить persons=10, gallery/probe sessions, conditions и cameras
по каждому person, SHA/file/name/burst duplicates и session leakage. Для двух
камер проверить probe coverage каждого лица на обеих.
PAD проверить attack types/sessions, devices, source identities, cameras,
duplicates, live gaps и protocol violations. Audit PASS проверяет глобальные
категории, но НЕ гарантирует все пять live conditions на каждой камере: отдельно
проверить иерархию live/<person>/<camera>/<session>/<condition>, а также все пять
attacks на каждой камере. Общий PASS не доказывает полноту каждого split.
Не удалять автоматически или молча исключать проблемные evidence.

## 5. Recognition calibration: только development

Выполнять только после gates выше:

```powershell
python -m scripts.calibrate_recognition --dataset "$recognitionRoot/development" --phase calibration --manifest "$recognitionRoot/manifest_development.csv" --csv "$recognitionRoot/calibration.csv" --json "$recognitionRoot/calibration.json"
if ($LASTEXITCODE -ne 0) { throw 'Recognition calibration failed; проверить ошибки' }
```

JSON: `genuine.count`, `impostor.count`, `FAR`, `FRR`, `eer`,
`recommended_candidates` (conservative/balanced/convenience threshold и
candidate_margin), `top1_accuracy`, `unknown_rejection_rate`,
`highest_impostor_similarities`, `per_person`, `per_condition`, `per_camera`,
`failures`, `images_failed`, `configured_threshold_evaluation`.
FAR/FRR верхнего уровня относятся к configured threshold, а candidate FAR/FRR
находятся внутри recommended_candidates. Margin sweep рассчитан при configured
threshold: выбранная candidate threshold/margin пара требует отдельной оценки.
Не утверждать, что её joint FAR/FRR уже измерены calibration отчётом.
Не смешивать pairwise FAR/FRR с итоговой идентификацией, учитывающей margin.

Для person01…person10 без дополнительных unknown probes значение
unknown_rejection_rate=null: NOT_MEASURED, не 0 и не 100%. Tool распознаёт
unknown probe IDs по префиксу unknown, однако существующий READY audit требует
gallery coverage для каждого лица. Этот workflow не добавляет фиктивную gallery
для unknown и не обходит audit gate. Unknown rejection остаётся незамеренной.

## 6. Held-out: human approval перед evaluation

Не запускать следующие команды до explicit human approval конкретной пары
threshold/margin из calibration. Input через Read-Host ниже — передача уже
одобренных чисел, сам по себе не заменяет approval. Не создавать candidate по test.
Сначала проверить новый root audit и отдельно held-out audit, включая splits.

```powershell
python -m scripts.recognition_dataset_audit --dataset "$recognitionRoot/test" --manifest "$recognitionRoot/manifest_test.csv" --report "$recognitionRoot/audit_test.json"
if ($LASTEXITCODE -ne 0) { throw 'STOP: held-out recognition audit failed' }
# Выполнять только ПОСЛЕ explicit human approval конкретных чисел:
$approvedThreshold = Read-Host 'Уже одобренный calibration threshold (точка как decimal separator)'
$approvedMargin = Read-Host 'Уже одобренный calibration margin (точка как decimal separator)'
python -m scripts.calibrate_recognition --dataset "$recognitionRoot/test" --phase test --threshold $approvedThreshold --margin $approvedMargin --manifest "$recognitionRoot/manifest_test.csv" --csv "$recognitionRoot/test_results.csv" --json "$recognitionRoot/test_results.json"
if ($LASTEXITCODE -ne 0) { throw 'Held-out evaluation failed' }
```

Reserved person08…person10 проверяют held-out identities с отдельной test
gallery. Это меньшая gallery из трёх лиц, а не тест неизвестных лиц против
development gallery и не доказательство масштаба production.
Без held-out split/approval/evaluation финальный IDENTITY_CALIBRATED=false,
даже если calibration tool выдаёт true. После evaluation отражать ошибки,
покрытие и принятие человеком результатов; tool true не заменяет эти условия.

## 7. PAD benchmark: одни evidence groups, три существующие модели

Только standardized pad_validation и PASS общего/development audits:

```powershell
python -m scripts.benchmark_pad_models --images $padRoot --phase calibration --models minifas fsvfm anti_spoof_mn3 --device cpu --report "$fieldReports/benchmark_development.json" --csv "$fieldReports/benchmark_development.csv" --video-sample-seconds 1.0 --max-frames-per-video 30
if ($LASTEXITCODE -ne 0) { throw 'PAD benchmark failed' }
$padBenchmark = Get-Content -Raw "$fieldReports/benchmark_development.json" | ConvertFrom-Json
if (-not $padBenchmark.same_evidence_groups -or @($padBenchmark.provider_failures.PSObject.Properties).Count -gt 0 -or @($padBenchmark.models.PSObject.Properties).Count -ne 3) {
    throw 'Неполный benchmark: нужны все три providers на одинаковых evidence groups'
}
```

CLI names: minifas = MiniFASNetV2, fsvfm = FS-VFM,
anti_spoof_mn3 = anti-spoof-mn3. Defaults используют уже существующие checkpoints
из deploy/models. Provider load failures означают NOT_EVALUATED, не успешное
сравнение двух оставшихся providers.
Из models читать overall APCER/BPCER/ACER, failure_rate, latency_ms
average/p50/p95, per_attack (все пять типов), per_condition (все пять live
условий), per_camera. Overall metrics агрегированы по evidence groups;
latency — по успешно обработанным frames. Failed groups и frame errors также
проверять в CSV: failure_rate не описывает все частичные ошибки внутри группы.
Не считать frames одного replay независимыми attack sessions.

Held-out PAD evaluation при отдельном полном test split и заранее выбранных
на development значениях (не по test):

```powershell
python -m scripts.pad_dataset_audit --dataset "$padRoot/test" --report "$fieldReports/audit_test.json"
if ($LASTEXITCODE -ne 0) { throw 'STOP: held-out PAD audit failed' }
# Передавать только заранее принятые development values:
$miniThreshold = Read-Host 'Принятый development minifas threshold'
$fsThreshold = Read-Host 'Принятый development fsvfm threshold'
$mn3Threshold = Read-Host 'Принятый development anti_spoof_mn3 threshold'
python -m scripts.benchmark_pad_models --images $padRoot --phase test --models minifas fsvfm anti_spoof_mn3 --device cpu --threshold "minifas=$miniThreshold" --threshold "fsvfm=$fsThreshold" --threshold "anti_spoof_mn3=$mn3Threshold" --report "$fieldReports/benchmark_test.json" --csv "$fieldReports/benchmark_test.csv"
if ($LASTEXITCODE -ne 0) { throw 'Held-out PAD benchmark failed' }
```

Снова проверить same_evidence_groups, все три providers, failures и coverage.
Если laptop_video, print, second-camera coverage или held-out split отсутствуют:
PAD_VALIDATED=false, BEST PAD=NONE, PRODUCTION_CANDIDATE не выдаётся.
Audit PASS/PAD_DATASET_COMPLETE сами по себе не снимают эти gates.
Даже при полном покрытии ranking не является автоматическим выбором модели:
нужны реальные метрики и принятые человеком acceptance limits; существующий
benchmark не одобряет limits автоматически. FS-VFM enforcement остаётся false.

## 8. Финальный field report

Текущее измеренное состояние: [PHASE7_FIELD_REPORT.md](PHASE7_FIELD_REPORT.md).
После сбора обновлять фактическими audit/metric results, не плановыми counts.
Всегда: PRODUCTION_THRESHOLDS_CHANGED=false,
FSVFM_ENFORCEMENT_ALLOWED=false, PRODUCTION_READY=false в рамках этой фазы.
