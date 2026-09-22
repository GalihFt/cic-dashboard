# Container Inventory Control Dashboard

Dashboard operasional kontainer berbasis NiceGUI dengan pipeline CSV ke satu fact table Parquet.

## Menjalankan pipeline

```powershell
python -m pipeline.build_dashboard_data
```

Input default: `source/data_cic_sample.csv`

Output default: dataset Parquet berpartisi di `data/dashboard/dashboard_activity/`.

Struktur partisi:

```text
data/dashboard/dashboard_activity/
├── activity_month=2026-05/part-00000.parquet
├── activity_month=2026-06/part-00000.parquet
└── activity_month=2026-07/part-00000.parquet
```

Dashboard menggunakan DuckDB untuk melakukan filter dan agregasi langsung pada
partisi Parquet tanpa memuat seluruh dataset ke Pandas.

Raw CSV hanya dibaca dan tidak diubah atau dihapus.

## Menjalankan dashboard

```powershell
python main.py
```

Kemudian buka `http://127.0.0.1:8080`.

## Impor file lokal berukuran besar

Impor bulan baru menggunakan mode append (default):

```powershell
python -m pipeline.import_local_months path/data-januari.csv path/data-februari.csv --append
```

Jika bulan sudah tersimpan, import akan ditolak. Untuk membangun ulang seluruh
dataset hanya dari file yang diberikan:

```powershell
python -m pipeline.import_local_months path/data-mei.csv path/data-juni.csv path/data-juli.csv --replace
```

Import diproses per chunk dan divalidasi melalui staging sebelum dataset aktif
diganti.

Menu yang tersedia:

- `Dashboard`: melihat KPI, chart, dan detail per kapal.
- `Database Management`: mengunggah, melihat, dan menghapus data bulanan.

## Pengelolaan data bulanan

1. Buka `http://127.0.0.1:8080/database`.
2. Unggah satu file CSV atau Excel (`.xlsx`) yang berisi satu bulan data.
3. Sistem memvalidasi struktur, bulan, dan event duplikat lalu menyimpannya sebagai data pengelolaan.
4. Tekan `Refresh Data` untuk mempublikasikan perubahan ke dashboard.
5. Kembali ke dashboard untuk melihat data terbaru.

Ketentuan:

- Satu file hanya boleh berisi satu bulan.
- Bulan yang sudah tersimpan tidak dapat diunggah kembali; hapus bulan lama terlebih dahulu jika ingin menggantinya.
- Event ID yang sudah tersimpan akan ditolak.
- Raw upload tidak disimpan. Hasil transformasi menunggu di dataset `data/dashboard/managed_activity/`.
- Dashboard membaca data yang sudah dipublikasikan dari dataset `data/dashboard/dashboard_activity/`.
- Upload atau penghapusan belum memengaruhi dashboard sampai tombol `Refresh Data` ditekan.
- `Last Refreshed` menunjukkan waktu terakhir data dipublikasikan ke dashboard.
- Penghapusan dilakukan per bulan dan membutuhkan konfirmasi di UI.

## Menjalankan pengujian

```powershell
python -m unittest discover -s tests -v
```

## Aturan metrik

- Semua KPI dan chart menggunakan TEU.
- Detail per kapal menggunakan quantity/box.
- Kontainer 20/21 feet dihitung 1 TEU, sedangkan 40 feet dihitung 2 TEU.
- Aktivitas ditentukan dari pasangan terarah `PREV_STATE -> CURRSTATE`.
- Kode rute dibuang dari vessel voyage, misalnya `TBI-13/2026 MKS-BMS` menjadi `TBI-13/2026`.

Definisi lengkap status dan aktivitas tersedia di `container-status-logic.md`.
