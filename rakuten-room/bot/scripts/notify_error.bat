@echo off
powershell -NoProfile -Command ^
[System.Media.SystemSounds]::Exclamation.Play(); ^
Add-Type -AssemblyName System.Speech; ^
$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; ^
$speak.Speak('エラーが発生しました。ログを確認してください')
