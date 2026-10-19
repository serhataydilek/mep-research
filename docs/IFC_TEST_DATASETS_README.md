# SmartSites HVAC / MEP - IFC test kit

**Dikkat:** Bu ZIP'in icinde IFC model dosyalarinin kendisi YOK. Bu ortamda dis kaynaklardan ikili dosya indirme engellendi. Bu, dosyalari senin bilgisayarinda indiren bir arac paketidir. IFC dosyalari kaynak sunucularindan gelir.

## Kurulum

1. ZIP'i AC. Icindeki `tools/` ve `docs/` klasorlerini, mevcut projenin ana dizinine cikart:

   `C:\Users\serfu\OneDrive\Belgeler\ChatGPT\hvac_research`

2. Proje dizininde PowerShell ac ve sunu calistir:

   `powershell -ExecutionPolicy Bypass -File .\tools\download_ifc_models.ps1`

3. Varsayilan indirilenler:
   - `validation/ifc_samples/external/small/fzk_house/arc.ifc`
   - `validation/ifc_samples/external/medium/dental_clinic/arc.ifc`
   - `validation/ifc_samples/external/medium/digital_hub/arc.ifc`
   - `validation/ifc_samples/external/medium/digital_hub/ventilation.ifc`

Istersen ek iki dosya:

   `powershell -ExecutionPolicy Bypass -File .\tools\download_ifc_models.ps1 -IncludeStructure -IncludeHeavy`

Bu komut Dental Clinic STR (~19.4 MB) ve MEP (~126 MB) modellerini de indirir.

4. `download_manifest.json` icinde SHA-256 ve boyut kayitlari bulunur. Her indirmenin ilk satiri STEP/IFC imzasiyla kontrol edilir. Bu kontrol tum modelin semantik/geometrik olarak gecerli oldugunu kanitlamaz.

## Lisans ve kaynak

IFC-Bench kaynak dataset: https://huggingface.co/datasets/sylvainHellin/ifc-bench

- fzk_house: https://huggingface.co/datasets/sylvainHellin/ifc-bench/tree/main/projects/fzk_house
- dental_clinic: https://huggingface.co/datasets/sylvainHellin/ifc-bench/tree/main/projects/dental_clinic
- digital_hub: https://huggingface.co/datasets/sylvainHellin/ifc-bench/tree/main/projects/digital_hub

Script mumkun oldugunda her proje icin `license.txt` ve `model_card.md` indirir. Yeniden dagitimda kaynak ve lisans sartlarina uy.

## Dikkat: Git ve OneDrive

Model dosyalarini Git'e pushlama. Proje baseline raporu `validation/ifc_samples/external/` yolunun ignore edildigini belirtiyor; yine de `git check-ignore` ve `git status` ile dogrula. OneDrive boyutlu dosyalari senkronize edebilir.

Gercek IFC dosyalarinda fiziksel route otomatik calisir diye varsayma: koordinat sistemi, geometri, portlar, giris/cikis ve izinler once denetlenmeli.
