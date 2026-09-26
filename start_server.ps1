Set-Location "F:\memU-main\memU-main\ai-girlfriend"
$py = ".\.venv\Scripts\python.exe"
$proc = Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app:app","--host","0.0.0.0","--port","8765" -WorkingDirectory "F:\memU-main\memU-main\ai-girlfriend" -PassThru -NoNewWindow
$proc.Id | Out-File "F:\memU-main\memU-main\ai-girlfriend\.server_pid" -Encoding utf8
Start-Sleep -Seconds 20
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/stories" -UseBasicParsing -TimeoutSec 5
    Write-Output "SUCCESS: HTTP $($r.StatusCode)"
} catch {
    Write-Output "API FAIL: $_"
    if ($proc.HasExited) { Write-Output "Process exited: code=$($proc.ExitCode)" }
    else { Write-Output "Process running: pid=$($proc.Id)" }
}