# 單硬碟雙系統（Windows 10/11 + Ubuntu）遠端切換完整指南

本文件完整記錄在一顆實體硬碟上安裝 Windows 10/11 與 Ubuntu 雙系統，並透過遠端（Windows Remote Desktop / Ubuntu SSH）以自動化腳本進行無縫雙向切換的架構、安裝流程、注意事項與腳本使用說明。

---

## 目錄
1. [專案架構與檔案清單](#專案架構與檔案清單)
2. [技術架構與切換原理](#技術架構與切換原理)
3. [事前準備與 BIOS/UEFI 設定](#事前準備與-biosuefi-設定)
4. [雙系統安裝步驟](#雙系統安裝步驟)
   - [階段一：安裝 Windows 10/11](#階段一安裝-windows-1011)
   - [階段二：安裝 Ubuntu](#階段二安裝-ubuntu)
5. [遠端環境設定](#遠端環境設定)
   - [Windows 端設定（RDP & OpenSSH）](#windows-端設定rdp--openssh)
   - [Ubuntu 端設定（OpenSSH & Sudoers）](#ubuntu-端設定openssh--sudoers)
6. [自動切換腳本使用方式](#自動切換腳本使用方式)
   - [步驟 1：填寫 config.json](#步驟-1填寫-configjson)
   - [步驟 2：執行切換](#步驟-2執行切換)
7. [常見問題與排查指南 (FAQ)](#常見問題與排查指南-faq)

---

## 專案架構與檔案清單

本專案提供一套跨平台 Python 核心邏輯與 Windows 批次檔 wrapper：

| 檔案 | 說明 |
| :--- | :--- |
| [`config.json`](config.json) | 連線設定檔（遠端主機 IP、SSH 連接埠、兩套系統之使用者帳密或金鑰） |
| [`switch_os.py`](switch_os.py) | 核心切換 Python 腳本（連線、OS 辨識、UEFI 開機順序修改與重啟下達） |
| [`reboot_to_windows.bat`](reboot_to_windows.bat) | 控制端一鍵切換至 Windows 批次檔 |
| [`reboot_to_ubuntu.bat`](reboot_to_ubuntu.bat) | 控制端一鍵切換至 Ubuntu 批次檔 |

---

## 技術架構與切換原理

### 1. 安裝順序：先 Windows，後 Ubuntu
* **Windows 安裝程式具侵略性**：Windows 會強制將自己的開機引導程式設為第一優先，且不會自動偵測或配置既有的 Linux 開機引導。
* **Ubuntu GRUB 具備完整偵測機制**：Ubuntu 安裝器（os-prober）能自動辨識已存在的 Windows Boot Manager，並與其共用同一顆硬碟上的 EFI 系統分區 (ESP)，兩者和平共存。

### 2. 遠端切換技術：UEFI BootNext 一次性引導
由於遠端主機無外接螢幕與實體鍵盤，**切換開機絕不能依賴人工於 BIOS/GRUB 選單手動選擇**。本方案採用 UEFI 韌體的「一次性引導 (BootNext)」機制：

* **從 Ubuntu 切換至 Windows**：
  1. 腳本透過 SSH 連線至 Ubuntu，偵測目前作業系統為 Linux。
  2. 執行 `sudo efibootmgr` 查詢 `Windows Boot Manager` 的開機項目編號（例如 `0000`）。
  3. 執行 `sudo efibootmgr -n 0000` 將 UEFI 下一次開機指向 Windows。
  4. 執行 `sudo reboot`。
* **從 Windows 切換至 Ubuntu**：
  1. 腳本透過 SSH 連線至 Windows，偵測目前作業系統為 Windows。
  2. 執行 `bcdedit /enum firmware` 自動解析出 `ubuntu` 的韌體開機 GUID。
  3. 執行 `bcdedit /set {fwbootmgr} bootsequence <GUID>` 將下一次開機順序設定為 Ubuntu。
  4. 執行 `shutdown /r /t 0`。
* **安全性與容錯優勢**：
  * 此機制**僅影響下一次開機**，不會永久竄改預設的開機順序。
  * 若切換後的系統因更新或當機重啟，硬體會自動退回預設開機項目，**避免遠端主機變磚失聯**。

### 3. SSH 雙系統共用 IP 與 Host Key 處理
* 雙系統位於同一部電腦、使用同一張實體網卡，MAC 位址相同，DHCP 路由器通常會配發**同一個 IP 位址**。
* Windows OpenSSH 與 Ubuntu OpenSSH 所產生的 Host Key 不同。一般 SSH 連線會跳出 `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`。
* 本方案之 [`switch_os.py`](switch_os.py) 透過 Paramiko 採用 `AutoAddPolicy()`，自動相容金鑰指紋切換，不會阻斷自動化流程。

---

## 事前準備與 BIOS/UEFI 設定

在開始安裝之前，請進入主機 BIOS 設定以下項目：

1. **開機模式 (Boot Mode)**：
   * 務必設定為 **純 UEFI**（關閉 CSM / Legacy Support）。
2. **硬碟控制模式 (SATA / NVMe Controller)**：
   * 務必設為 **AHCI**。
   * **切勿**開啟 Intel RST (VMD / RAID)，否則 Linux 核心會找不到 NVMe/SATA 硬碟。
3. **安全開機 (Secure Boot)**：
   * Ubuntu 官方核心具備 Canonical 數位簽章與 Shim 支援，通常可開啟。
   * 若預期安裝未簽名之第三方閉源驅動（例如特定顯卡/網卡驅動），建議安裝期間先暫時關閉。
4. **【極度重要】關閉 Windows 快速啟動 (Fast Startup)**：
   * Windows 快速啟動本質為混合休眠（Hybrid Hibernation），關機時會將 NTFS 硬碟標記為鎖定狀態。
   * 若未停用，Ubuntu 將無法掛載或存取 Windows 磁區，且 Windows 可能會攔截 UEFI BootNext 請求直接熱啟動。
5. **BitLocker 全碟加密**：
   * Windows 11 在部分硬體會預設啟用 BitLocker。雙系統切換可能觸發 TPM 安全防護要求輸入 48 位修復金鑰。
   * 建議在安裝完成後先備份修復金鑰，或直接關閉 BitLocker。

---

## 雙系統安裝步驟

### 階段一：安裝 Windows 10/11
1. 插入 Windows 安裝隨身碟，開機進入安裝畫面。
2. 授權條款後，選擇「**自訂：只安裝 Windows (進階)**」。
3. **磁碟分割關鍵原則**：
   * **不要把整顆硬碟分滿給 C 槽**。
   * 例如 1TB 硬碟：建立 400GB 給 Windows，其餘約 600GB 空間保持為 **未分配空間 (Unallocated Space)**，保留給後續的 Ubuntu。
   * Windows 會自動在硬碟開頭建立 EFI 系統分區 (ESP，通常為 100MB~500MB)。
4. 依提示完成安裝並進入 Windows 桌面。

---

### 階段二：安裝 Ubuntu
1. 插入 Ubuntu 安裝隨身碟，開機選單中選擇帶有 **UEFI:** 前綴的隨身碟開機。
2. 進入安裝程式，在「Installation Type（安裝類型）」步驟：
   * 若有「**Install Ubuntu alongside Windows Boot Manager**」選項，系統會自動使用剛才留下的未分配空間。
   * 若選擇「**Something else（手動分割）**」：
     * 找到剛才預留的「Free Space / Unallocated Space」。
     * 新增分區：檔案系統選擇 `ext4`，掛載點設為 `/`。
     * **Boot loader device（開機引導程式安裝位置）**：選擇既有的 EFI 系統分區（例如 `/dev/nvme0n1p1` 或 `/dev/sda1`）。
3. 完成安裝後拔除隨身碟並重新啟動。

---

## 遠端環境設定

### Windows 端設定（RDP & OpenSSH）

1. **關閉快速啟動**：
   以系統管理員身分開啟 PowerShell，執行：
   ```powershell
   powercfg /h off
   ```
2. **啟用遠端桌面 (RDP)**：
   開啟「設定」$\rightarrow$「系統」$\rightarrow$「遠端桌面」$\rightarrow$ 切換為「開啟」。
3. **安裝並啟動 OpenSSH 伺服器**：
   以系統管理員身分開啟 PowerShell，執行：
   ```powershell
   # 安裝 OpenSSH 伺服器
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

   # 啟動服務並設定為開機自動啟動
   Start-Service sshd
   Set-Service -Name sshd -StartupType 'Automatic'

   # 確認防火牆規則已開啟
   Get-NetFirewallRule -Name *ssh*
   ```
4. **確認 SSH 帳號權限**：
   確保預計用於 SSH 登入的 Windows 帳號屬於本機 `Administrators` 群組，以便遠端執行 `bcdedit` 與 `shutdown` 指令。

---

### Ubuntu 端設定（OpenSSH & Sudoers）

1. **安裝並啟用 OpenSSH 伺服器**：
   ```bash
   sudo apt update
   sudo apt install -y openssh-server
   sudo systemctl enable --now ssh
   ```
2. **設定 sudo 免密碼（推薦）**：
   為了讓切換腳本無需在無介面環境下卡在 sudo 密碼輸入，建議設定指定指令免密碼。
   執行：
   ```bash
   sudo visudo
   ```
   在檔案最底端加入以下設定（請將 `<your_username>` 替換為實際使用者名稱）：
   ```text
   <your_username> ALL=(ALL) NOPASSWD: /usr/sbin/efibootmgr, /sbin/reboot, /bin/systemctl
   ```
3. **驗證 UEFI 開機項目**：
   執行：
   ```bash
   sudo efibootmgr
   ```
   確認清單中包含類似 `Boot0000* Windows Boot Manager` 的項目。

---

## 自動切換腳本使用方式

### 步驟 1：填寫 config.json

在控制端機器（執行切換動作的電腦）開啟 [`config.json`](config.json)，填入主機資訊：

```json
{
  "host": "192.168.1.100",
  "port": 22,
  "timeout": 180,
  "windows": {
    "username": "your_windows_user",
    "password": "your_windows_password",
    "key_filename": null
  },
  "ubuntu": {
    "username": "your_ubuntu_user",
    "password": "your_ubuntu_password",
    "sudo_password": null,
    "key_filename": null
  }
}
```

> **欄位說明**：
> - `host`：目標雙系統主機的 IP 位址。
> - `port`：SSH 連接埠，預設為 22。
> - `timeout`：重開機連線輪詢逾時秒數，預設為 180 秒（亦可在指令透過 `--timeout` 指定）。
> - `windows` / `ubuntu`：兩端的登入憑證。若採用 SSH Key 登入，可將 `key_filename` 指向私鑰路徑，密碼設為 `null`。
> - `sudo_password`：若 Ubuntu 端未配置免密碼 sudo，請填入此欄位，腳本將自動代入。

---

### 步驟 2：執行切換（含開機完成與 OS 自動驗證）

腳本下達重開機後，會**自動持續輪詢 SSH** 直到目標主機重開機完成、成功連線，並向系統確認目前運行的 OS 已正確切換為目標系統。

#### 欲切換至 Windows：
雙擊執行 [`reboot_to_windows.bat`](reboot_to_windows.bat)。
* 若目前已是 Windows：腳本輸出 `Target OS is already Windows. No action taken.` 並立即退出。
* 若目前是 Ubuntu：腳本透過 `efibootmgr` 設定下次開機載入 Windows 並重啟。隨後**自動等待並輪詢 SSH**，確認 Windows 已經開機且 OpenSSH 上線，最後提示成功訊息。此時即可立刻透過 Windows 遠端桌面 (RDP) 連線。

#### 欲切換至 Ubuntu：
雙擊執行 [`reboot_to_ubuntu.bat`](reboot_to_ubuntu.bat)。
* 若目前已是 Ubuntu：腳本輸出 `Target OS is already Ubuntu. No action taken.` 並立即退出。
* 若目前是 Windows：腳本透過 `bcdedit` 設定下次開機載入 Ubuntu 並重啟。隨後**自動等待並輪詢 SSH**，確認 Ubuntu 已經開機且 SSH 上線，最後提示成功訊息。此時即可立即開始 SSH 工作。

#### 進階執行參數（可於命令列傳入）：
```cmd
# 指定等待逾時時間為 300 秒
python switch_os.py --target windows --timeout 300

# 不等待重開機結果（下達重啟後直接結束，即原本的 fire-and-forget 行為）
python switch_os.py --target ubuntu --no-wait
```

---

## 常見問題與排查指南 (FAQ)

### Q1：執行切換後，等了很久主機都沒有重新連上線？
1. **檢查快速啟動**：請確認 Windows 的「快速啟動」是否確實關閉。
2. **主機板開機順序設定**：部分老舊主機板可能忽略 `BootNext`。請確認 BIOS 韌體版本為最新版本。
3. **檢查網路連線**：兩套系統的網路介面卡是否都正常取得 IP，建議在路由器端綁定 MAC 與固定 IP。

### Q2：在 Windows 執行 bcdedit 出現「存取被拒 (Access Denied)」？
* OpenSSH 登入的使用者必須為 Windows 系統的本機管理員 (`Administrators` 群組)。
* 請在 Windows 的「電腦管理」$\rightarrow$「本機使用者和群組」中確認該帳號具備管理員權限。

### Q3：在 Ubuntu 執行 efibootmgr 報錯「EFI variables are not supported on this system」？
* 這代表 Ubuntu 安裝時是以 Legacy BIOS (CSM) 模式啟動安裝，而非 UEFI 模式。
* 請確認 BIOS 已完全關閉 CSM，並重新使用 UEFI 隨身碟開機安裝。

### Q4：手動 SSH 連線時出現 Host Key 警報？
* 因同一 IP 在切換 OS 後會由不同的 SSH 伺服器回應（Windows OpenSSH vs Ubuntu OpenSSH）。
* 本專案的 Python 腳本會自動處理此問題。若平時手動使用終端機 SSH，可在連線參數加上：
  ```bash
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null user@192.168.1.100
  ```
