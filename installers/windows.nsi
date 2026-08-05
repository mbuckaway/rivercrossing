; SPDX-License-Identifier: GPL-3.0-only
; RiverCrossing Windows installer -- the unsigned half of E9.1.2,
; pulled forward in EPIC 1 Phase 9 (R-01, spec.md section 10 / 14;
; Phase 9 amendment: NSIS replaces Inno Setup).
;
; Per-user by design: no UAC prompt, the payload lands under
; $LOCALAPPDATA\Programs, the Start-menu entry and the uninstall
; registry key stay in the current user's hive. Authenticode signing
; remains EPIC 9 (E9.1.2); the guide documents the SmartScreen
; "More info -> Run anyway" step for this unsigned build.
;
; Compiled by `nox -s winsetup`; the contract is pinned by
; tests/unit/test_windows_nsi.py and the artifact is smoked by
; tests/functional/test_winsetup_smoke.py. Every machine-specific
; input arrives as a compile-time define, so no version or path is
; ever hard-coded here:
;
;   makensis -DAPPVERSION=<rivercrossing.__version__>
;            -DPAYLOAD_DIR=<built payload dir, native separators>
;            -DOUTFILE=<absolute output path for the setup .exe>
;            installers/windows.nsi
;
; Measured platform quirks, all encoded here or in the callers:
; relative compile-time paths (the branding icon below) resolve
; against this script's own directory; makensis on macOS needs a
; UTF-8 locale; and the File filespec below joins with a backslash
; because Windows makensis finds no files behind a forward-slash
; glob (measured on windows-latest) while POSIX makensis converts
; the backslash (measured locally).

!ifndef APPVERSION
  !error "APPVERSION not defined -- pass -DAPPVERSION=<rivercrossing.__version__>"
!endif
!ifndef PAYLOAD_DIR
  !error "PAYLOAD_DIR not defined -- pass -DPAYLOAD_DIR=<built payload directory>"
!endif
!ifndef OUTFILE
  !error "OUTFILE not defined -- pass -DOUTFILE=<output installer path>"
!endif

Name "RiverCrossing"
OutFile "${OUTFILE}"
Unicode true
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\RiverCrossing"
Icon "branding/rivercrossing.ico"
UninstallIcon "branding/rivercrossing.ico"

Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*"
  CreateShortcut "$SMPROGRAMS\RiverCrossing.lnk" "$INSTDIR\rivercrossing.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing" "DisplayName" "RiverCrossing"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing" "DisplayVersion" "${APPVERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing" "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing" "QuietUninstallString" '"$INSTDIR\uninstall.exe" /S'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing" "DisplayIcon" "$INSTDIR\rivercrossing.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing" "InstallLocation" "$INSTDIR"
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$SMPROGRAMS\RiverCrossing.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RiverCrossing"
  RMDir /r "$INSTDIR"
SectionEnd
