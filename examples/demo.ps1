# Run with a locally running KellyLab API. Creates a new demo user and session.
param([string]$BaseUrl = 'http://127.0.0.1:8000')
$ErrorActionPreference = 'Stop'
$credentials = @{
    email = "demo-$([guid]::NewGuid().ToString('N'))@example.com"
    password = "Demo-$([guid]::NewGuid().ToString('N'))!"
}
$null = Invoke-RestMethod "$BaseUrl/api/auth/register/" -Method Post -ContentType 'application/json' -Body ($credentials | ConvertTo-Json)
$token = Invoke-RestMethod "$BaseUrl/api/auth/token/" -Method Post -ContentType 'application/json' -Body ($credentials | ConvertTo-Json)
$headers = @{ Authorization = "Bearer $($token.access)" }
$null = Invoke-RestMethod "$BaseUrl/api/auth/me/" -Method Patch -Headers $headers -ContentType 'application/json' -Body '{"target_position":"Python Developer","experience_level":"junior"}'
$session = Invoke-RestMethod "$BaseUrl/api/sessions/" -Method Post -Headers $headers -ContentType 'application/json' -Body '{"name":"Interview preparation"}'
$sessionUrl = "$BaseUrl/api/sessions/$($session.id)"
$body = @{ text = Get-Content (Join-Path $PSScriptRoot 'questions.txt') -Raw -Encoding UTF8 } | ConvertTo-Json
$null = Invoke-RestMethod "$sessionUrl/import/" -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
$state = Invoke-RestMethod "$sessionUrl/generate/" -Method Post -Headers $headers
$deadline = (Get-Date).AddMinutes(6)
while ($state.status -eq 'processing' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $state = Invoke-RestMethod "$sessionUrl/" -Headers $headers
}
if ($state.status -ne 'ready') { throw "Session status: $($state.status). $($state.error_message)" }
$cards = Invoke-RestMethod "$sessionUrl/questions/" -Headers $headers
$cards.results | Select-Object id, text, answer | Format-List
$outputFile = Join-Path $PSScriptRoot 'demo-export.txt'
Invoke-WebRequest "$sessionUrl/export/" -Headers $headers -OutFile $outputFile
Write-Host "Session $($session.id) is ready. Export: $outputFile"
