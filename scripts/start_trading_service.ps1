# scripts/start_trading_service.ps1
# Production Windows Daemon Launcher & Auto-Recovery Watchdog for Binance Testnet Trading Bot

$ErrorActionPreference = "Continue"
$BotDirectory = "D:\MT5\python_bot"
Set-Location $BotDirectory

Write-Host "=========================================================="
Write-Host "  BINANCE TESTNET TRADING BOT — 24/7 WINDOWS SERVICE"
Write-Host "=========================================================="
Write-Host "WorkingDirectory: $BotDirectory"
Write-Host "Started at: $(Get-Date)"

$MaxRestarts = 50
$RestartCount = 0

while ($RestartCount -lt $MaxRestarts) {
    Write-Host "[WATCHDOG] Launching python bot.py (Attempt $($RestartCount + 1))..."
    
    # Run bot daemon and redirect logs to bot.log
    $Process = Start-Process -FilePath "python" -ArgumentList "bot.py" -WorkingDirectory $BotDirectory -PassThru -NoNewWindow -Wait
    
    $ExitCode = $Process.ExitCode
    $RestartCount++
    
    Write-Host "[WATCHDOG] Bot process exited with code $ExitCode at $(Get-Date)"
    
    if ($ExitCode -eq 0) {
        Write-Host "[WATCHDOG] Clean exit requested. Shutting down service."
        break
    } else {
        Write-Host "[WATCHDOG] Process crashed or aborted. Restarting in 5 seconds..."
        Start-Sleep -Seconds 5
    }
}
