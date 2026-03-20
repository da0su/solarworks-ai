@echo off
powershell -NoProfile -Command ^
[System.Media.SystemSounds]::Asterisk.Play(); ^
Add-Type -AssemblyName System.Speech; ^
$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer; ^
$speak.Speak('修正が完了しました')
