$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $root "mcp_server.cmd"
$codex = Get-Command codex -ErrorAction Stop

Write-Host "Добавление локального MCP-сервера fire-audit..."
& $codex.Source mcp add fire-audit -- $env:ComSpec /d /c $server
if ($LASTEXITCODE -ne 0) {
    throw "Codex не смог добавить MCP-сервер."
}

Write-Host ""
Write-Host "Сервер добавлен. Перезапустите приложение Codex."
Write-Host "После перезапуска введите /mcp и проверьте наличие fire-audit."

