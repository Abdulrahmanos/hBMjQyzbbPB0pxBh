param([Parameter(Mandatory=$true)][string]$ChaptersPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$chapters = Get-Content -LiteralPath $ChaptersPath -Raw | ConvertFrom-Json
$speech = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $speech.SelectVoice('Microsoft David Desktop')
    $speech.Rate = 0
    $speech.Volume = 100
    foreach ($chapter in $chapters) {
        $speech.SetOutputToWaveFile([string]$chapter.audio_path)
        $speech.Speak([string]$chapter.narration)
        $speech.SetOutputToNull()
    }
} finally { $speech.Dispose() }
