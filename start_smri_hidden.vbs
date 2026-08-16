Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projectDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = projectDir & "\start_smri_service.ps1"
logPath = projectDir & "\logs\smri_launcher.log"

If Not fso.FolderExists(projectDir & "\logs") Then
    fso.CreateFolder(projectDir & "\logs")
End If

Set logFile = fso.OpenTextFile(logPath, 8, True)
logFile.WriteLine Now & " - Lanzando SMRI desde: " & scriptPath
logFile.Close

shell.CurrentDirectory = projectDir
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & scriptPath & Chr(34), 0, False
