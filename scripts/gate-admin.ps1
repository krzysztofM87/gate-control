[CmdletBinding()]
param(
    [string]$BaseUrl = "http://tools.malmaz.com/gate-control",

    [string]$AdminToken = $env:GATE_ADMIN_TOKEN,
    [string]$DeviceSecret = $env:GATE_DEVICE_SECRET,
    [string]$DeviceId = "gate-main",

    [ValidateSet("debug", "new-token", "new-all", "list-tokens", "list-commands", "poll", "ack")]
    [string]$Action = "debug",

    [ValidateSet("open_1", "open_2", "open_both")]
    [string]$GateTarget = "open_1",

    [string]$Label = "",
    [int]$ValidHours = 72,
    [int]$MaxUses = 10,
    [int]$CooldownSeconds = 5,

    [string]$CommandId = ""
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd("/")

function Require-AdminToken {
    if ([string]::IsNullOrWhiteSpace($AdminToken)) {
        throw "Brak AdminToken. Ustaw: `$env:GATE_ADMIN_TOKEN = '...'"
    }
}

function Require-DeviceSecret {
    if ([string]::IsNullOrWhiteSpace($DeviceSecret)) {
        throw "Brak DeviceSecret. Ustaw: `$env:GATE_DEVICE_SECRET = '...'"
    }
}

function Get-AdminHeaders {
    Require-AdminToken
    return @{
        "X-Admin-Token" = $AdminToken
    }
}

function Get-DeviceHeaders {
    Require-DeviceSecret
    return @{
        "X-Device-Id" = $DeviceId
        "X-Device-Secret" = $DeviceSecret
    }
}

function New-GateToken {
    param(
        [string]$TokenLabel,
        [ValidateSet("open_1", "open_2", "open_both")]
        [string]$Target
    )

    $body = @{
        label = $TokenLabel
        device_id = $DeviceId
        gate_target = $Target
        valid_hours = $ValidHours
        max_uses = $MaxUses
        open_cooldown_seconds = $CooldownSeconds
    } | ConvertTo-Json -Compress

    $result = Invoke-RestMethod `
        -Method Post `
        -Uri "$BaseUrl/admin/tokens" `
        -Headers (Get-AdminHeaders) `
        -ContentType "application/json" `
        -Body $body

    return [PSCustomObject]@{
        Label = $TokenLabel
        Gate = $result.gate_target
        Url = $result.public_url
        ValidTo = $result.valid_to
        MaxUses = $result.max_uses
        Token = $result.token
    }
}

switch ($Action) {
    "debug" {
        Invoke-RestMethod -Method Get -Uri "$BaseUrl/debug/state"
    }

    "new-token" {
        if ([string]::IsNullOrWhiteSpace($Label)) {
            $Label = "token $GateTarget"
        }

        New-GateToken -TokenLabel $Label -Target $GateTarget
    }

    "new-all" {
        $tokens = @()
        $tokens += New-GateToken -TokenLabel "test bramy 1" -Target "open_1"
        $tokens += New-GateToken -TokenLabel "test bramy 2" -Target "open_2"
        $tokens += New-GateToken -TokenLabel "test obu bram" -Target "open_both"

        $tokens | Format-Table Label, Gate, Url, ValidTo, MaxUses -AutoSize
    }

    "list-tokens" {
        Invoke-RestMethod `
            -Method Get `
            -Uri "$BaseUrl/admin/tokens" `
            -Headers (Get-AdminHeaders)
    }

    "list-commands" {
        Invoke-RestMethod `
            -Method Get `
            -Uri "$BaseUrl/admin/commands" `
            -Headers (Get-AdminHeaders)
    }

    "poll" {
        Invoke-RestMethod `
            -Method Get `
            -Uri "$BaseUrl/api/device/poll?device_id=$DeviceId" `
            -Headers (Get-DeviceHeaders)
    }

    "ack" {
        if ([string]::IsNullOrWhiteSpace($CommandId)) {
            throw "Brak CommandId. Użyj: -CommandId 'uuid-komendy'"
        }

        $body = @{
            device_id = $DeviceId
            command_id = $CommandId
            status = "done"
            message = "manual powershell ack"
        } | ConvertTo-Json -Compress

        Invoke-RestMethod `
            -Method Post `
            -Uri "$BaseUrl/api/device/ack" `
            -Headers (Get-DeviceHeaders) `
            -ContentType "application/json" `
            -Body $body
    }
}
