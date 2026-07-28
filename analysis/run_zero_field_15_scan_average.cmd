@echo off
cd /d C:\Lab_Py\nv_control
.venv\Scripts\python.exe -u analysis\average_zero_field_scans.py "data\zero_field_2026-07-20_10-59-13_scan_*.npz" --output "data\zero_field_2026-07-20_10-59-13_average.npz" --report "data\zero_field_2026-07-20_10-59-13_averaging_report.txt" > "data\zero_field_2026-07-20_10-59-13_averaging.log" 2> "data\zero_field_2026-07-20_10-59-13_averaging.error.log"
