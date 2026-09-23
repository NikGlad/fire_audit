$ErrorActionPreference = "Stop"
$appRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $appRoot "mcp_server.cmd"
$codex = Get-Command codex -ErrorAction Stop

Write-Host "Adding local MCP server fire-audit..."
& $codex.Source mcp add fire-audit -- $env:ComSpec /d /c $server
if ($LASTEXITCODE -ne 0) {
    throw "Codex could not add the MCP server."
}

Write-Host ""
Write-Host "Server added. Restart Codex, then use /mcp to verify fire-audit."
