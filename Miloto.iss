; Miloto 安装包脚本 (Inno Setup 6)
; 打包正式版 dist/Miloto/ 文件夹，带 LICENSE，自动建桌面+开始菜单快捷方式

#define MyAppName "Miloto"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Miloto"
#define MyAppURL "https://github.com/"
#define MyAppExeName "Miloto.exe"

[Setup]
; 基本标识
AppId={{A1B2C3D4-E5F6-7890-ABCD-1234567890EF}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Miloto 微信桥接工具

; 安装目录（默认 Program Files，用户可点"浏览"改任意目录）
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 单用户安装不提权，写 VirtualStore 也可；允许所有用户则提权
PrivilegesRequired=lowest
AlwaysShowDirOnReadyPage=yes
AllowNoIcons=no

; 输出
OutputDir=installer
OutputBaseFilename=Miloto-Setup
SetupIconFile=miloto.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; 64 位-aware，避免 WOW64 文件系统重定向干扰
ArchitecturesInstallIn64BitMode=x64
ArchitecturesAllowed=x64

; 卸载
UninstallDisplayName={#MyAppName}
Uninstallable=yes
CreateUninstallRegKey=yes

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Files]
; 整文件夹打进安装包（Miloto.exe + 依赖 dll）
Source: "dist\Miloto\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 第三方插件目录：随包附带示例插件；授予用户写权限，便于在不提权下增删插件
Source: "plugins\*"; DestDir: "{app}\plugins"; Flags: ignoreversion recursesubdirs createallsubdirs; Permissions: users-modify
; LICENSE（MIT + Akasha 出处声明）
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; 开始菜单程序组
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式(&D)"; GroupDescription: "附加任务:"; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Messages]
; 中文提示
SetupAppTitle=Miloto 安装向导
WelcomeLabel1=欢迎使用 Miloto 安装向导
