' run_hidden.vbs - bat ファイルを cmd 窓を表示せずに実行する wrapper.
' CEO 2026-05-16: 「コマンド開いて画面占領するのやめて」指示で導入.
' 使い方: wscript.exe run_hidden.vbs <bat-path> [args...]
Set WshShell = CreateObject("WScript.Shell")
If WScript.Arguments.Count < 1 Then
    WScript.Echo "Usage: wscript.exe run_hidden.vbs <bat-path> [args...]"
    WScript.Quit 1
End If
cmd = """" & WScript.Arguments(0) & """"
For i = 1 To WScript.Arguments.Count - 1
    cmd = cmd & " " & WScript.Arguments(i)
Next
' Run with hidden window (0), wait for completion (True)
rc = WshShell.Run("cmd /c " & cmd, 0, True)
WScript.Quit rc
