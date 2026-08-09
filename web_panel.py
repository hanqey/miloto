

import hmac
import json
import logging
import os
import secrets
import sys
import threading
import time

from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from core import runtime
from core.loggingx import build_logger, get_log_lines
from core.security import hash_password, is_hashed, needs_upgrade, verify_password
from core.settings import write_path
from net.portutil import ensure_port_free

log = build_logger("miloto.web")

_ENGINE = None

_DASHBOARD = {
    "username": "miloto",
    "password_hash": "",
    "is_initial": False,
    "initial_password": "",
}
_SESSIONS = {}
_PASSWORD_MIN_LEN = 6

_LOGIN_FAILS = {}
_LOGIN_MAX_FAILS = 5
_LOGIN_LOCK_SECS = 60

def init_dashboard_creds():

    before = (_DASHBOARD.get("username"), _DASHBOARD.get("password_hash"))
    account = _ENGINE.settings.dashboard
    _DASHBOARD["username"] = account["username"]
    stored = account["password"]
    if stored:
        _DASHBOARD["password_hash"] = stored
        _DASHBOARD["is_initial"] = False
        _DASHBOARD["initial_password"] = ""
        if not is_hashed(stored):

            try:
                hashed = hash_password(stored)
                write_path(_ENGINE.settings.data, "dashboard.password", hashed)
                _ENGINE._write_yaml(_ENGINE.settings.data)
                _DASHBOARD["password_hash"] = hashed
                log.warning("[Web] config.yaml 里的控制台密码是明文，已自动改存哈希（登录密码不变）")
            except Exception as exc:
                log.warning(f"[Web] 明文密码自动升级失败，将在登录成功后重试: {exc}")
    elif _DASHBOARD.get("is_initial") and _DASHBOARD.get("initial_password"):

        pass
    else:
        initial = secrets.token_urlsafe(8)
        _DASHBOARD["initial_password"] = initial
        _DASHBOARD["password_hash"] = hash_password(initial)
        _DASHBOARD["is_initial"] = True
        log.warning("[Web] 控制台未设置登录密码")

    if (_DASHBOARD["username"], _DASHBOARD["password_hash"]) != before:
        _SESSIONS.clear()

def save_new_password(plain_text):

    hashed = hash_password(plain_text)
    _ENGINE.apply_config({"dashboard.username": _DASHBOARD["username"],
                          "dashboard.password": hashed})
    _DASHBOARD["password_hash"] = hashed
    _DASHBOARD["is_initial"] = False
    _DASHBOARD["initial_password"] = ""
    return hashed

def print_startup_banner(host, port):

    box = [
        "****************",
        f" 网页控制台已启动: http://{host}:{port}",
        f" 网页控制台已就绪: http://127.0.0.1:{port}  （公网地址取决于 web_host 配置）",
    ]
    if _DASHBOARD.get("is_initial"):
        box.append(f" 默认账户为: {_DASHBOARD['username']}")
        box.append(f" 默认密码为: {_DASHBOARD['initial_password']}")
    box.append("****************")
    print("\n".join(box), file=sys.stderr)

def refresh_dashboard_creds():

    init_dashboard_creds()

PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Miloto</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='10' fill='%234e9d7c'/></svg>">
<style>
@import url('https://fonts.googleapis.com/css2?family=Quicksand:wght@600;700&display=swap');
:root{
  --bg:#e8f0eb;
  --surface:#ffffff;
  --surface-2:#f4f9f6;
  --border:#e4efe9;
  --border-strong:#d6e7dd;
  --text:#33433c;
  --text-soft:#6f8a7e;
  --text-faint:#9bb3a7;
  --brand:#4e9d7c;
  --brand-2:#7cc4a4;
  --brand-ink:#3f7d63;
  --shadow-sm:0 1px 2px rgba(28,52,40,.05);
  --shadow-md:0 6px 20px -8px rgba(28,52,40,.16);
  --shadow-lg:0 14px 40px -14px rgba(28,52,40,.24);
  --radius:16px;
  --radius-sm:11px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);height:100vh;color:var(--text);display:flex;margin:0;overflow:hidden}

/* ===== 主容器 ===== */
.container{display:flex;width:100vw;height:100vh;background:var(--surface);overflow:hidden;border:none}

/* ===== 侧边栏（深绿实体面板，借鉴 NapCat / AstrBot 的「深侧栏 + 浅内容」布局）===== */
.sidebar{width:206px;min-width:206px;background:linear-gradient(180deg,#2c5645 0%,#1e3a2f 100%);display:flex;flex-direction:column;padding:18px 12px;gap:2px;border-right:none;height:100vh;box-shadow:3px 0 16px rgba(18,38,30,.20);position:relative}
.sidebar .logo{display:flex;align-items:center;gap:10px;padding:4px 8px 16px;margin-bottom:6px;color:#fff;font-family:'Quicksand','Segoe UI',sans-serif;font-weight:700;font-size:18px;letter-spacing:.5px}
.sidebar .logo .mark{width:28px;height:28px;border-radius:9px;background:linear-gradient(135deg,#7cc4a4,#4e9d7c);display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,.28)}
.sidebar .logo .mark svg{width:17px;height:17px}
.sidebar .nav-section{font-size:11px;color:rgba(214,235,225,.5);padding:16px 12px 6px;font-weight:600;letter-spacing:1px}
.sidebar .nav-item{width:100%;min-height:42px;border-radius:10px;display:flex;align-items:center;gap:12px;cursor:pointer;transition:background .16s ease,color .16s ease;color:rgba(222,240,231,.80);font-size:13.5px;font-weight:500;padding:0 12px;border:none;background:transparent;text-align:left;font-family:inherit;line-height:1}
.sidebar .nav-item svg{width:18px;height:18px;flex:none;opacity:.82;transition:opacity .16s ease}
.sidebar .nav-item:hover{background:rgba(255,255,255,.08);color:#fff}
.sidebar .nav-item:hover svg{opacity:1}
.sidebar .nav-item.active{background:rgba(124,196,164,.20);color:#fff;box-shadow:inset 3px 0 0 #8fd0b3}
.sidebar .nav-item.active svg{opacity:1}
.sidebar .sidebar-footer{margin-top:auto;padding:12px 12px 2px;border-top:1px solid rgba(255,255,255,.10);font-size:11px;color:rgba(222,240,231,.45);text-align:center;letter-spacing:.3px}

/* ===== 内容区 ===== */
.content{flex:1;padding:30px 34px;overflow-y:auto;display:flex;flex-direction:column;gap:18px;height:100vh;background:var(--surface)}
.content::-webkit-scrollbar{width:5px}
.content::-webkit-scrollbar-thumb{background:#cfe3d8;border-radius:6px}

.tab-page{display:none;flex-direction:column;gap:16px;height:100%}
.tab-page.active{display:flex}

/* ===== 标题栏 ===== */
.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:12px;flex-wrap:wrap}
.header h1{font-size:24px;font-weight:700;display:flex;align-items:baseline;gap:10px;color:var(--text)}
.header h1 .en{font-family:'Quicksand','Segoe UI',sans-serif;background:linear-gradient(135deg,#3f7d63,#4e9d7c);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:1px}
.header .badge{font-size:11px;color:var(--brand-ink);background:#e3f0e8;padding:4px 12px;border-radius:20px;font-weight:600;border:1px solid var(--border)}
.header-actions{display:flex;gap:10px;flex-wrap:wrap}

/* ===== 引导（首页，参照 AstrBot 引导风格）===== */
.guide{display:flex;flex-direction:column;gap:16px}
.guide-hero{background:linear-gradient(135deg,#ffffff,#eef6f1);border:1px solid var(--border);border-radius:var(--radius);padding:20px 24px;box-shadow:var(--shadow-sm)}
.guide-title{font-size:18px;font-weight:700;color:var(--brand-ink);font-family:'Quicksand','Segoe UI',sans-serif}
.guide-sub{font-size:13px;color:var(--text-soft);margin-top:6px;line-height:1.65}
.guide-steps{display:flex;gap:14px;flex-wrap:wrap}
.step-card{flex:1;min-width:160px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:18px;box-shadow:var(--shadow-sm);display:flex;flex-direction:column;gap:8px;transition:box-shadow .2s ease,transform .2s ease}
.step-card:hover{box-shadow:var(--shadow-md);transform:translateY(-2px)}
.step-num{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#7cc4a4,#4e9d7c);color:#fff;font-size:13px;font-weight:700;display:flex;align-items:center;justify-content:center}
.step-title{font-size:14px;font-weight:600;color:var(--text)}
.step-desc{font-size:12px;color:var(--text-soft);line-height:1.65}

/* ===== 按钮组 ===== */
.btn{padding:10px 18px;border:none;border-radius:var(--radius-sm);font-size:13px;font-weight:600;cursor:pointer;transition:all .2s ease;display:inline-flex;align-items:center;gap:6px}
.btn:disabled{opacity:0.4;cursor:not-allowed}
.btn:active:not(:disabled){transform:scale(0.97)}
.btn-pink{background:linear-gradient(135deg,#7cc4a4,#4e9d7c);color:#fff;box-shadow:var(--shadow-md)}
.btn-pink:hover:not(:disabled){box-shadow:0 6px 18px rgba(94,157,124,0.38);transform:translateY(-1px)}
.btn-outline{background:var(--surface);color:var(--brand-ink);border:1.5px solid var(--border-strong)}
.btn-outline:hover:not(:disabled){background:#eaf4ef;border-color:var(--brand)}
.btn-primary{background:linear-gradient(135deg,#7cc4a4,#4e9d7c);color:#fff;box-shadow:var(--shadow-md)}
.btn-primary:hover:not(:disabled){box-shadow:0 6px 18px rgba(94,157,124,0.38);transform:translateY(-1px)}

/* ===== 日志 ===== */
.log-box{flex:1;min-height:120px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px;font-size:12px;font-family:'Cascadia Code','Fira Code','Consolas',monospace;color:#5a6a62;overflow-y:auto;line-height:1.6;white-space:pre-wrap}
.log-box:empty::before{content:'等待连接...';color:var(--text-faint)}
.log-box::-webkit-scrollbar{width:5px}
.log-box::-webkit-scrollbar-thumb{background:#cfe0d6;border-radius:6px}
/* 日志级别横排开关（运行日志页上方工具条） */
.log-levels{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;padding:11px 16px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm)}
.log-levels-label{font-size:12px;color:var(--brand-ink);font-weight:600;margin-right:2px}
.lvl-toggle{display:inline-flex;align-items:center;gap:7px;cursor:pointer;font-size:12px;color:var(--text);user-select:none;line-height:1}
.lvl-toggle input{appearance:none;-webkit-appearance:none;width:36px;height:19px;border-radius:10px;background:#cdded4;position:relative;cursor:pointer;transition:background .22s ease;outline:none;margin:0;flex:none}
.lvl-toggle input::after{content:'';position:absolute;top:2px;left:2px;width:15px;height:15px;border-radius:50%;background:#fff;transition:transform .22s ease;box-shadow:0 1px 2px rgba(0,0,0,.18);will-change:transform}
.lvl-toggle input:checked{background:var(--brand)}
.lvl-toggle input:checked::after{transform:translateX(17px)}
.lvl-name{font-weight:700;letter-spacing:.4px;font-family:'Cascadia Code','Fira Code',monospace}
.lvl-toggle.lv-debug .lvl-name{color:#9b7cc4}
.lvl-toggle.lv-info .lvl-name{color:var(--brand)}
.lvl-toggle.lv-warning .lvl-name{color:#d99a3e}
.lvl-toggle.lv-error .lvl-name{color:#d9695f}

/* ===== 设置页面 ===== */
.settings-scroll{flex:1;overflow-y:auto;padding-right:4px}
.settings-scroll::-webkit-scrollbar{width:5px}
.settings-scroll::-webkit-scrollbar-thumb{background:#cfe0d6;border-radius:6px}
.settings-group{margin-bottom:20px}
.settings-group h3{font-size:13px;font-weight:600;color:var(--brand-ink);margin-bottom:10px;padding-bottom:6px;border-bottom:1.5px solid var(--border)}
.settings-row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:6px}
.settings-field{flex:1;min-width:160px}
.settings-field label{display:block;font-size:11px;color:var(--text-soft);margin-bottom:4px;font-weight:500}
.settings-field input,.settings-field select,.settings-field textarea{width:100%;padding:8px 11px;border:1.5px solid var(--border);border-radius:var(--radius-sm);font-size:12px;outline:none;transition:border .2s,box-shadow .2s;background:var(--surface);color:var(--text);font-family:inherit}
.settings-field input:focus,.settings-field select:focus,.settings-field textarea:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(94,157,124,0.12)}
.settings-field textarea{resize:vertical;min-height:36px}
.field-hint{font-size:11px;color:var(--text-faint);margin-top:4px;line-height:1.45}
.settings-field select{cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236f9a85'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 10px center;padding-right:28px}

.save-bar{display:flex;justify-content:flex-end;align-items:center;gap:12px;padding-top:10px;border-top:1px solid var(--border)}
.save-bar .save-msg{font-size:12px;color:#43a047;opacity:0;transition:opacity .4s}
.save-bar .save-msg.show{opacity:1}

/* ===== Toast ===== */
.toast{position:fixed;top:24px;left:50%;transform:translateX(-50%);padding:11px 24px;border-radius:14px;font-size:13px;font-weight:500;z-index:999;opacity:0;transition:opacity .35s,transform .35s;pointer-events:none;box-shadow:var(--shadow-md)}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.toast.success{background:#eaf7ec;color:#2e7d32;border:1px solid #c8e6c9}
.toast.error{background:#fdecea;color:#c62828;border:1px solid #ffcdd2}
.toast.info{background:#e3f0e8;color:#2e6b53;border:1px solid #bfe0d0}

/* ===== 连接设置（NapCat 式多连接）===== */
.conn-section{margin-bottom:16px}
.conn-section-title{font-size:13px;font-weight:600;color:var(--brand-ink);margin-bottom:10px;padding-bottom:6px;border-bottom:1.5px solid var(--border)}
.conn-list{display:flex;flex-direction:column;gap:10px}
.conn-empty{font-size:12px;color:var(--text-faint);padding:14px 16px;background:var(--surface);border:1px dashed var(--border-strong);border-radius:var(--radius-sm)}
.conn-card{display:flex;align-items:center;gap:14px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow-sm);flex-wrap:wrap;transition:box-shadow .2s ease,border-color .2s ease}
.conn-card.editing{flex-direction:column;align-items:stretch;box-shadow:var(--shadow-md)}
.conn-info{flex:1;min-width:160px}
.conn-name{font-size:14px;font-weight:600;color:var(--text)}
.conn-url{font-size:11px;color:var(--text-soft);margin-top:3px;word-break:break-all}
.conn-state{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-soft);min-width:72px}
.conn-state .dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot.online{background:#4caf50;box-shadow:0 0 0 3px rgba(76,175,80,0.18)}
.dot.offline{background:#cfe0d6}
.dot.disabled{background:#d8d8d8}
.conn-actions{display:flex;align-items:center;gap:8px}
.conn-edit-fields{display:flex;gap:10px;flex-wrap:wrap}
.conn-edit-fields .settings-field{flex:1;min-width:140px}

/* 插件管理（NapCat 风格：卡片网格 + 搜索 + 已安装/市场 子页） */
.plugin-ver{font-size:11px;font-weight:600;color:#6f9a85;background:#eaf4ef;border:1px solid #cfe0d6;border-radius:8px;padding:1px 7px;margin-left:6px;vertical-align:middle}

.plugin-toolbar{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.plugin-tabs{display:inline-flex;background:var(--surface-2);border:1px solid var(--border);border-radius:12px;padding:4px;gap:4px}
.ptab{border:none;background:transparent;color:var(--text-soft);font-size:13px;font-weight:600;padding:7px 16px;border-radius:9px;cursor:pointer;font-family:inherit;transition:background .18s ease,color .18s ease;display:inline-flex;align-items:center;gap:6px;line-height:1}
.ptab:hover{color:var(--text)}
.ptab.active{background:#fff;color:var(--brand-ink);box-shadow:0 1px 4px rgba(94,157,124,0.14)}
.ptab i{font-style:normal;font-size:11px;font-weight:700;background:var(--brand);color:#fff;border-radius:20px;padding:1px 8px;min-width:18px;text-align:center}
.plugin-search{flex:1;max-width:280px;min-width:160px}
.plugin-search input{width:100%;padding:9px 14px;border:1.5px solid var(--border);border-radius:11px;font-size:13px;outline:none;background:var(--surface);color:var(--text);font-family:inherit;transition:border .2s,box-shadow .2s}
.plugin-search input:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(94,157,124,0.12)}

.plugin-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(248px,1fr));gap:14px;align-content:start}
.plugin-card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:16px;box-shadow:var(--shadow-sm);display:flex;flex-direction:column;gap:12px;transition:box-shadow .2s ease,border-color .2s ease,transform .2s ease}
.plugin-card:hover{box-shadow:0 6px 20px rgba(63,125,99,0.12);border-color:var(--border-strong);transform:translateY(-2px)}
.pc-head{display:flex;align-items:center;gap:12px}
.pc-icon{width:42px;height:42px;min-width:42px;border-radius:12px;background:linear-gradient(135deg,#7cc4a4,#4e9d7c);color:#fff;font-size:15px;font-weight:700;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(78,157,124,0.28)}
.pc-title{flex:1;min-width:0}
.pc-name{font-size:14px;font-weight:600;color:var(--text);display:flex;align-items:center;gap:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pc-author{font-size:11px;color:var(--text-soft);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pc-gh{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-soft);transition:background .18s ease,border-color .18s ease,color .18s ease}
.pc-gh:hover{background:#eaf4ef;color:var(--brand-ink);border-color:var(--brand)}
.pc-gh svg{width:16px;height:16px;fill:currentColor}
.ce-title{font-weight:600;margin-bottom:6px}
.ce-sub{font-size:12px;color:var(--text-soft);line-height:1.5}
.pc-desc{font-size:12px;color:var(--text-soft);line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:38px}
.pc-foot{display:flex;align-items:center;justify-content:space-between;gap:10px;padding-top:10px;border-top:1px solid var(--border)}
.pc-status{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-soft)}
.pc-status .dot{width:8px;height:8px}
.pc-actions{display:flex;align-items:center;gap:8px}
.btn-ghost{border:1px solid var(--border-strong);background:var(--surface-2);color:var(--text-soft);font-size:12px;font-weight:600;padding:7px 14px;border-radius:10px;cursor:pointer;font-family:inherit;transition:background .18s ease,border-color .18s ease,color .18s ease}
.btn-ghost:hover{background:#eaf4ef;color:var(--brand-ink);border-color:var(--brand)}

/* 骨架屏（加载态） */
.plugin-card.skel{pointer-events:none}
.plugin-card.skel .pc-icon.sk{background:#e6efe9}
.sk-line{height:10px;border-radius:6px;background:linear-gradient(90deg,#e9f1ec 25%,#f3f8f5 37%,#e9f1ec 63%);background-size:400% 100%;animation:sk-shimmer 1.3s ease infinite}
.sk-line.w90{width:90%}.sk-line.w70{width:70%}.sk-line.w60{width:60%}.sk-line.w40{width:40%}
@keyframes sk-shimmer{0%{background-position:100% 0}100%{background-position:0 0}}
.switch-line{display:flex;align-items:center;justify-content:space-between;gap:12px}
.switch-line .switch{flex:none}
.plugin-form{display:flex;flex-direction:column;gap:4px}
.plist{display:flex;flex-direction:column;gap:8px;margin-top:6px;padding:10px;border:1px dashed var(--border-strong);border-radius:var(--radius-sm);background:var(--surface-2)}
.pitem{display:flex;align-items:center;gap:8px}
.pitem input,.pitem select{flex:1;min-width:0;padding:7px 10px;border:1.5px solid var(--border);border-radius:var(--radius-sm);font-size:12px;outline:none;background:var(--surface);color:var(--text);font-family:inherit}
.pitem input:focus,.pitem select:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(94,157,124,0.12)}
.pitem-del{flex:none;padding:6px 11px;border-radius:50%}
.plist .btn{margin-top:2px;align-self:flex-start}

/* 开关 */
.switch{position:relative;display:inline-block;width:42px;height:24px}
.switch input{opacity:0;width:0;height:0}
.switch .slider{position:absolute;cursor:pointer;inset:0;background:#cfe0d6;border-radius:24px;transition:.25s}
.switch .slider:before{content:'';position:absolute;height:18px;width:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.25s;box-shadow:0 1px 3px rgba(0,0,0,0.2)}
.switch input:checked + .slider{background:linear-gradient(135deg,#7cc4a4,#4e9d7c)}
.switch input:checked + .slider:before{transform:translateX(18px)}

/* ===== 自定义弹窗（圆角矩形，替代原生 confirm）===== */
.modal-overlay{position:fixed;inset:0;background:rgba(58,74,66,0.28);backdrop-filter:blur(3px);display:none;align-items:center;justify-content:center;z-index:1000;opacity:0;transition:opacity .22s}
.modal-overlay.show{display:flex;opacity:1}
.modal-box{background:#fff;border-radius:18px;padding:22px 24px;width:340px;max-width:86vw;box-shadow:0 16px 48px rgba(63,125,99,0.24);transform:translateY(10px) scale(0.97);transition:transform .22s}
.modal-overlay.show .modal-box{transform:translateY(0) scale(1)}
/* 表单类弹窗（如修改密码）：更宽、留白更足 */
.modal-box.wide{width:420px;border-radius:22px;padding:28px 32px 24px}
.modal-box.wide .modal-title{font-size:19px;margin-bottom:6px}
.modal-box.wide .modal-body{margin-bottom:24px}
.modal-box.wide .modal-actions{gap:12px}
.modal-title{font-size:16px;font-weight:700;color:var(--text);margin-bottom:8px;font-family:'Quicksand','Segoe UI',sans-serif}
.modal-body{font-size:13px;color:var(--text-soft);line-height:1.7;margin-bottom:18px;word-break:break-word}
.modal-actions{display:flex;justify-content:flex-end;gap:10px}

/* ===== 施工中占位（插件 / 插件市场）===== */
.under-construction{display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;gap:12px;min-height:320px}
.under-construction .big{font-size:48px;font-weight:800;color:var(--brand-2);font-family:'Quicksand','Segoe UI',sans-serif;letter-spacing:3px}
.under-construction .sub{font-size:14px;color:var(--text-faint)}

/* ===== 登录遮罩（AstrBot 风：用户名 + 密码）===== */
.login-mask{position:fixed;inset:0;background:linear-gradient(160deg,#eaf4ef,#dcebe3);display:flex;align-items:center;justify-content:center;z-index:2000;padding:24px}
.login-box{background:#fff;border-radius:24px;padding:44px 42px 34px;width:420px;max-width:92vw;box-shadow:0 24px 64px rgba(63,125,99,0.26);display:flex;flex-direction:column;align-items:center}
.login-logo{font-size:32px;font-weight:800;color:var(--brand-ink);font-family:'Quicksand','Segoe UI',sans-serif;letter-spacing:1.5px;line-height:1.2}
.login-sub{font-size:14px;color:var(--text-soft);text-align:center;margin:8px 0 28px}
/* 表单本体：竖排 flex，字段之间留足呼吸感（JS 用 display:flex/none 切换） */
.login-form{width:100%;display:flex;flex-direction:column;gap:20px}
.login-field{display:flex;flex-direction:column;gap:8px}
.login-label{font-size:13px;font-weight:600;color:var(--text-soft);letter-spacing:.2px}
.login-input{width:100%;padding:13px 16px;border:1.5px solid var(--border);border-radius:12px;font-size:15px;outline:none;background:var(--surface);color:var(--text);font-family:inherit;transition:border .2s,box-shadow .2s}
.login-input::placeholder{color:var(--text-faint)}
.login-input:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(94,157,124,0.14);background:#fff}
.login-err{font-size:12.5px;color:#c62828;min-height:18px;text-align:center;line-height:1.5;margin:-10px 0 -6px}
.login-btn{width:100%;padding:14px;border:none;border-radius:12px;background:linear-gradient(135deg,#7cc4a4,#4e9d7c);color:#fff;font-size:16px;font-weight:700;letter-spacing:2px;cursor:pointer;font-family:inherit;box-shadow:0 6px 18px rgba(78,157,124,0.28);transition:opacity .2s,transform .1s,box-shadow .2s}
.login-btn:hover{opacity:.94;box-shadow:0 8px 22px rgba(78,157,124,0.34)}
.login-btn:active{transform:scale(.985)}
.login-hint{font-size:12px;color:var(--text-faint);text-align:center;line-height:1.7;margin-top:20px;padding-top:16px;border-top:1px solid var(--border)}

/* ===== 侧边栏退出登录 ===== */
.nav-logout{margin:8px 14px 0;padding:9px 12px;border:1px solid rgba(255,255,255,0.22);background:rgba(255,255,255,0.08);color:rgba(255,255,255,0.82);font-size:12px;font-weight:600;border-radius:11px;cursor:pointer;font-family:inherit;transition:background .18s ease,color .18s ease;display:flex;align-items:center;justify-content:center;gap:6px}
.nav-logout:hover{background:rgba(255,255,255,0.16);color:#fff}
</style>
</head>
<body>

<div class="toast" id="toast"></div>

<!-- ===== 登录遮罩（AstrBot 风：用户名 + 密码）===== -->
<div class="login-mask" id="loginMask">
  <div class="login-box">
    <div class="login-logo">Miloto</div>
    <!-- 登录模式 -->
    <div class="login-form" id="loginForm">
      <div class="login-sub" style="margin:0">网页控制台登录</div>
      <div class="login-field">
        <label class="login-label" for="loginUser">用户名</label>
        <input class="login-input" id="loginUser" type="text" placeholder="请输入用户名" autocomplete="username">
      </div>
      <div class="login-field">
        <label class="login-label" for="loginPass">密码</label>
        <input class="login-input" id="loginPass" type="password" placeholder="请输入密码" autocomplete="current-password" onkeydown="if(event.key==='Enter')doLogin()">
      </div>
      <div class="login-err" id="loginErr"></div>
      <button class="login-btn" onclick="doLogin()">登 录</button>
      <div class="login-hint">首次启动的随机密码已打印在程序控制台日志中</div>
    </div>
    <!-- 首次登录后强制改密模式 -->
    <div class="login-form" id="forceForm" style="display:none">
      <div class="login-sub" style="margin:0">首次登录需设置新密码</div>
      <div class="login-field">
        <label class="login-label" for="forcePass">新密码</label>
        <input class="login-input" id="forcePass" type="password" placeholder="至少 6 位" autocomplete="new-password" onkeydown="if(event.key==='Enter')doForceChange()">
      </div>
      <div class="login-field">
        <label class="login-label" for="forcePass2">确认新密码</label>
        <input class="login-input" id="forcePass2" type="password" placeholder="再次输入新密码" autocomplete="new-password" onkeydown="if(event.key==='Enter')doForceChange()">
      </div>
      <div class="login-err" id="forceErr"></div>
      <button class="login-btn" onclick="doForceChange()">设置并进入</button>
      <div class="login-hint">出于安全，首次登录需设置新的控制台密码</div>
    </div>
  </div>
</div>

<!-- ===== 自定义弹窗（圆角矩形）===== -->
<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)hideModal()">
  <div class="modal-box">
    <div class="modal-title" id="modalTitle"></div>
    <div class="modal-body" id="modalBody"></div>
    <div class="modal-actions" id="modalActions"></div>
  </div>
</div>

<div class="container">

<!-- ===== 侧边栏（深绿实体面板，NapCat / AstrBot 风格）===== -->
<div class="sidebar">
  <div class="logo">
    <span class="mark"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 20A7 7 0 0 1 4 13C4 8 8 4 20 4c0 8-4 13-9 13z"/><path d="M5 20c2-4 5-7 9-9"/></svg></span>
    <span>Miloto</span>
  </div>
  <div class="nav-section">主导航</div>
  <button class="nav-item active" data-tab="home" onclick="switchTab('home')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-10"/></svg>
    <span>首页</span>
  </button>
  <button class="nav-item" data-tab="logs" onclick="switchTab('logs')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h12"/><path d="M8 12h12"/><path d="M8 18h12"/><circle cx="3.5" cy="6" r=".6" fill="currentColor"/><circle cx="3.5" cy="12" r=".6" fill="currentColor"/><circle cx="3.5" cy="18" r=".6" fill="currentColor"/></svg>
    <span>日志</span>
  </button>
  <button class="nav-item" data-tab="plugin" onclick="switchTab('plugin')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4a2 2 0 1 0-4 0v2H8a2 2 0 1 0 0 4h2v2a2 2 0 1 0 4 0v-2h2a2 2 0 1 1 0 4h-2v2a2 2 0 1 1-4 0"/></svg>
    <span>插件</span>
  </button>
  <button class="nav-item" data-tab="pluginmarket" onclick="switchTab('plugin','pluginmarket');pluginSubTab('market')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>
    <span>插件市场</span>
  </button>
  <div class="nav-section">系统</div>
  <button class="nav-item" data-tab="connection" onclick="switchTab('connection')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>
    <span>连接设置</span>
  </button>
  <button class="nav-item" data-tab="basic" onclick="switchTab('basic')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
    <span>基础设置</span>
  </button>
  
  <button class="nav-logout" style="background:transparent;color:#9fd8bf;box-shadow:none" onclick="showChangePwd()">修改密码</button>
  <button class="nav-logout" onclick="doLogout()">退出登录</button>
  <div class="sidebar-footer">Miloto · 微信桥接</div>
</div>

<!-- ===== 内容区 ===== -->
<div class="content">

  <!-- ===== 首页（AstrBot 引导风格，无状态卡/启停按钮）===== -->
  <div class="tab-page active" id="page-home">
    <div class="header">
      <h1><span class="en">Miloto</span></h1>
    </div>
    <div class="guide">
      <div class="guide-hero">
        <div class="guide-title">微信 &#8596; AstrBot 桥接控制台</div>
        <div class="guide-sub">将微信消息实时转发到 AstrBot，让机器人通过 OneBot v11 回消息。在「连接设置」里添加并启用连接即可开始桥接。</div>
      </div>
      <div class="guide-steps">
        <div class="step-card">
          <div class="step-num">1</div>
          <div class="step-title">配置连接</div>
          <div class="step-desc">在「连接设置」点击「创建 WebSocket 连接」「创建 WeFlow 连接」，填入地址并启用。</div>
        </div>
        <div class="step-card">
          <div class="step-num">2</div>
          <div class="step-title">自动连接</div>
          <div class="step-desc">启用的连接会随程序启动自动连接，可单独开关、单独配置、创建多个。</div>
        </div>
        <div class="step-card">
          <div class="step-num">3</div>
          <div class="step-title">查看日志</div>
          <div class="step-desc">在「日志」页实时查看运行状态与消息流转，排查问题一目了然。</div>
        </div>
      </div>
    </div>
  </div>

  <!-- ===== 日志（仅显示日志）===== -->
  <div class="tab-page" id="page-logs">
    <div class="header">
      <h1>运行日志</h1>
    </div>
    <div class="log-levels" id="logLevels">
      <span class="log-levels-label">日志级别</span>
      <label class="lvl-toggle lv-debug"><input type="checkbox" data-level="debug" onchange="toggleLevel('debug', this.checked)"><span class="lvl-name">DEBUG</span></label>
      <label class="lvl-toggle lv-info"><input type="checkbox" data-level="info" onchange="toggleLevel('info', this.checked)"><span class="lvl-name">INFO</span></label>
      <label class="lvl-toggle lv-warning"><input type="checkbox" data-level="warning" onchange="toggleLevel('warning', this.checked)"><span class="lvl-name">WARNING</span></label>
      <label class="lvl-toggle lv-error"><input type="checkbox" data-level="error" onchange="toggleLevel('error', this.checked)"><span class="lvl-name">ERROR</span></label>
    </div>
    <div class="log-box" id="log">等待连接...</div>
  </div>

  <!-- ===== 连接设置（NapCat 式多连接）===== -->
  <div class="tab-page" id="page-connection">
    <div class="header">
      <h1>连接设置</h1>
      <div class="header-actions">
        <button class="btn btn-pink" onclick="createConn('ob')">＋ 创建 WebSocket 连接</button>
        <button class="btn btn-pink" onclick="createConn('weflow')">＋ 创建 WeFlow 连接</button>
      </div>
    </div>
    <div class="conn-section">
      <div class="conn-section-title">WebSocket 连接（AstrBot OneBot v11）</div>
      <div class="conn-list" id="conn-ob"></div>
    </div>
    <div class="conn-section">
      <div class="conn-section-title">WeFlow 连接</div>
      <div class="conn-list" id="conn-weflow"></div>
    </div>
  </div>

  <!-- ===== 基础设置（机器人 + Web 监听）===== -->
  <div class="tab-page" id="page-basic">
    <div class="header">
      <h1>基础设置</h1>
      <div class="badge">config.yaml</div>
    </div>
    <div class="settings-scroll" id="basicForm"></div>
    <div class="save-bar">
      <span class="save-msg" id="saveMsgBasic">&#10003; 已保存</span>
      <button class="btn btn-pink" onclick="saveConfig('basic')">&#128190; 保存设置</button>
    </div>
  </div>

  <!-- ===== 插件（NapCat 风格：卡片网格 + 搜索 + 已安装/市场 子页）===== -->
  <div class="tab-page" id="page-plugin">
    <div class="header">
      <h1>插件</h1>
      <div class="badge">Plugins</div>
    </div>
    <div class="plugin-toolbar">
      <div class="plugin-tabs">
        <button class="ptab active" data-ptab="installed" onclick="pluginSubTab('installed')">已安装 <i id="pluginCount">0</i></button>
        <button class="ptab" data-ptab="market" onclick="pluginSubTab('market')">市场</button>
      </div>
      <div class="plugin-search">
        <input id="pluginSearch" type="text" placeholder="搜索插件名称 / 描述" oninput="renderPluginGrid(); renderMarketGrid();">
      </div>
    </div>
    <div id="pluginInstalled">
      <div class="plugin-grid" id="pluginGrid"></div>
    </div>
    <div id="pluginMarket" style="display:none">
      <div class="plugin-grid" id="marketGrid"></div>
    </div>
  </div>

</div>
</div>

<script>
// ===== 工具 =====
function toast(msg, type) {
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + type + ' show';
  setTimeout(function(){t.className='toast'}, 2500);
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];}); }

// ===== 鉴权（AstrBot 风：用户名+密码，成功后带 token 访问 API）=====
function getToken() { return localStorage.getItem('miloto_token') || ''; }
function setToken(t) { localStorage.setItem('miloto_token', t); }
function clearToken() { localStorage.removeItem('miloto_token'); }

function api(url, opts) {
  // 所有内部 API 调用统一走这里，自动带上登录 token；401 则弹出登录层
  opts = opts || {};
  opts.headers = opts.headers || {};
  var t = getToken();
  if (t) opts.headers['Authorization'] = 'Bearer ' + t;
  return window.fetch(url, opts).then(function(r){
    if (r.status === 401) { showLogin(); throw new Error('未授权'); }
    return r;
  });
}
function showLogin() {
  clearToken();
  var m = document.getElementById('loginMask');
  if (m) m.style.display = 'flex';
  var err = document.getElementById('loginErr');
  if (err) err.textContent = '';
  var err2 = document.getElementById('forceErr');
  if (err2) err2.textContent = '';
  document.getElementById('loginForm').style.display = 'flex';   // .login-form 是竖排 flex，不能用 block
  document.getElementById('forceForm').style.display = 'none';
  var u = document.getElementById('loginUser');
  if (u) u.focus();
}
function doLogin() {
  var u = document.getElementById('loginUser');
  var p = document.getElementById('loginPass');
  var err = document.getElementById('loginErr');
  api('/api/login', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({username: u.value, password: p.value})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok && res.token) {
        setToken(res.token);
        if (res.must_change_pw) { showForceChange(); return; }
        var m = document.getElementById('loginMask');
        if (m) m.style.display = 'none';
        startAppLoop();
      } else {
        err.textContent = (res.error || '登录失败') + '（请检查用户名/密码，随机密码见程序控制台日志）';
      }
    }).catch(function(e){ err.textContent = '登录失败: ' + e.message; });
}
function showForceChange() {
  // 已用随机密码登录、但尚未设置新密码：强制展示改密表单（保留已登录会话）
  var m = document.getElementById('loginMask');
  if (m) m.style.display = 'flex';
  document.getElementById('loginForm').style.display = 'none';
  document.getElementById('forceForm').style.display = 'flex';   // 同上
  var err = document.getElementById('forceErr');
  if (err) err.textContent = '';
  var np = document.getElementById('forcePass');
  if (np) np.focus();
}
function doForceChange() {
  var p = document.getElementById('forcePass');
  var p2 = document.getElementById('forcePass2');
  var err = document.getElementById('forceErr');
  if (p.value.length < 6) { err.textContent = '密码至少 6 位'; return; }
  if (p.value !== p2.value) { err.textContent = '两次输入的密码不一致'; return; }
  api('/api/setup', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({password: p.value})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) {
        var m = document.getElementById('loginMask');
        if (m) m.style.display = 'none';
        startAppLoop();
      } else {
        err.textContent = res.error || '设置失败';
      }
    }).catch(function(e){ err.textContent = '设置失败: ' + e.message; });
}
function doLogout() {
  clearToken();
  showLogin();
}
function showChangePwd() {
  // 弹出修改密码弹窗（AstrBot 风）：当前密码 + 新密码 + 确认。
  // 复用登录页的 .login-form/.login-field 样式，字段之间留白一致、不再挤在一起。
  var html = ''+
    '<div class="login-form" style="gap:18px">'+
      '<div class="login-field"><label class="login-label" for="cpOld">当前密码</label>'+
        '<input class="login-input" type="password" id="cpOld" placeholder="请输入当前密码" autocomplete="current-password"></div>'+
      '<div class="login-field"><label class="login-label" for="cpNew">新密码</label>'+
        '<input class="login-input" type="password" id="cpNew" placeholder="至少 6 位" autocomplete="new-password"></div>'+
      '<div class="login-field"><label class="login-label" for="cpNew2">确认新密码</label>'+
        '<input class="login-input" type="password" id="cpNew2" placeholder="再次输入新密码" autocomplete="new-password" onkeydown="if(event.key===\'Enter\')doChangePassword()"></div>'+
      '<div class="login-err" id="cpErr" style="margin:-8px 0 -6px"></div>'+
    '</div>';
  showModal('修改控制台密码', html, [
    {label:'取消', cls:'btn-outline', onClick: hideModal},
    {label:'确认修改', cls:'btn-primary', onClick: doChangePassword},
  ], {wide: true});
  setTimeout(function(){ var o=document.getElementById('cpOld'); if(o) o.focus(); }, 50);
}
function doChangePassword() {
  var old = document.getElementById('cpOld').value;
  var nw = document.getElementById('cpNew').value;
  var nw2 = document.getElementById('cpNew2').value;
  var err = document.getElementById('cpErr');
  if (nw.length < 6) { err.textContent = '新密码至少 6 位'; return; }
  if (nw !== nw2) { err.textContent = '两次输入的新密码不一致'; return; }
  api('/api/change-password', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({old_password: old, new_password: nw})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) {
        hideModal();
        // 密码变更后所有会话失效，强制重新登录
        clearToken();
        showLogin();
        var le = document.getElementById('loginErr');
        if (le) le.textContent = '密码已修改，请重新登录';
      } else {
        err.textContent = res.error || '修改失败';
      }
    }).catch(function(e){ err.textContent = '修改失败: ' + e.message; });
}
function showSave(msgId, text) {
  var el = document.getElementById(msgId);
  el.textContent = text;
  el.className = 'save-msg show';
  setTimeout(function(){el.className='save-msg'}, 3000);
}

// ===== 自定义弹窗（圆角矩形，替代原生 confirm）=====
function showModal(title, bodyHtml, buttons, opts) {
  // buttons: [{label, cls, onClick}]；opts.wide=true 用更宽的表单版式（如修改密码）
  opts = opts || {};
  var box = document.querySelector('#modalOverlay .modal-box');
  if (box) box.classList.toggle('wide', !!opts.wide);
  document.getElementById('modalTitle').textContent = title;
  document.getElementById('modalBody').innerHTML = bodyHtml;
  var act = document.getElementById('modalActions');
  act.innerHTML = '';
  (buttons || []).forEach(function(b){
    var btn = document.createElement('button');
    btn.className = 'btn ' + (b.cls || 'btn-outline');
    btn.textContent = b.label;
    btn.onclick = function(){ if (b.onClick) b.onClick(); };
    act.appendChild(btn);
  });
  document.getElementById('modalOverlay').classList.add('show');
}
function hideModal(){ document.getElementById('modalOverlay').classList.remove('show'); }

// ===== Tab 切换 =====
function switchTab(name, navKey) {
  var nav = navKey || name;
  document.querySelectorAll('.tab-page').forEach(function(p){p.classList.remove('active')});
  document.getElementById('page-' + name).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(function(n){n.classList.remove('active')});
  var navEl = document.querySelector('[data-tab="' + nav + '"]');
  if (navEl) navEl.classList.add('active');
  if (name === 'connection') loadConnections();
  if (name === 'basic') loadConfig();
  if (name === 'plugin') { loadPlugins(); pluginSubTab('installed'); }
  if (name === 'logs') refresh(true);   // 切到日志页立即拉最新并滚到底部（看最新日志）
}

// ===== 面板刷新（日志 + 连接状态）=====
function refresh(forceBottom) {
  api('/status').then(function(r){return r.json()}).then(function(s){
    var logEl = document.getElementById('log');
    if (logEl) {
      var isAtBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
      logEl.textContent = s.log || '';
      // forceBottom（切到日志 tab 时）= 无条件滚到最新；否则仅在用户本就停在底部时跟随
      if (forceBottom || isAtBottom) logEl.scrollTop = logEl.scrollHeight;
    }
    // 同步日志级别开关状态（来自 /status 的 log_levels）
    if (s.log_levels) applyLogLevels(s.log_levels);
  }).catch(function(){});
  // 连接状态：仅在不编辑时刷新卡片（避免打断编辑表单）
  if (editingKey === null && document.getElementById('page-connection').classList.contains('active')) {
    loadConnections();
  }
}

// ===== 日志级别横排开关 =====
function applyLogLevels(state) {
  // state: {debug, info, warning, error}（bool）；按 checkbox 的 data-level 设置勾选
  document.querySelectorAll('#logLevels input[type="checkbox"]').forEach(function(cb){
    var lv = cb.getAttribute('data-level');
    if (state && Object.prototype.hasOwnProperty.call(state, lv)) {
      cb.checked = !!state[lv];
    }
  });
}
function toggleLevel(level, on) {
  // 单个级别开关变化：即时 POST 到后端，写盘并生效（无需重启）
  api('/api/loglevel', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({level: level, on: on}),
  }).then(function(r){return r.json()}).then(function(res){
    if (!res.ok) {
      // 失败回滚勾选状态
      var cb = document.querySelector('#logLevels input[data-level="'+level+'"]');
      if (cb) cb.checked = !on;
    }
  }).catch(function(){
    var cb = document.querySelector('#logLevels input[data-level="'+level+'"]');
    if (cb) cb.checked = !on;
  }).finally(function(){
    // 开关变更后立即刷新日志显示：否则要等 3 秒轮询才看到旧行消失，体感「反应慢」
    refresh(true);
  });
}

// ===== 配置表单（基础设置，Miloto 真实键）=====
var FORM_BASIC = [
  {title:'机器人', fields:[
    {key:'bot.names', label:'机器人昵称（多个用逗号隔开）', type:'text', ph:'Miloto'},
    {key:'bot.wxid', label:'机器人 wxid', type:'text', ph:'wxid_xxx'},
  ]},
  {title:'Web 监听', fields:[
    {key:'web.host', label:'Web 监听地址（需重启生效）', type:'select', opts:[{v:'0.0.0.0',l:'0.0.0.0（允许公网访问）'},{v:'127.0.0.1',l:'127.0.0.1（仅本机访问）'}]},
    {key:'web.port', label:'Web 面板端口（需重启生效）', type:'number', ph:'8127'},
  ]},
  {title:'文件存储', fields:[
    {key:'files.storage_dir', label:'文件存放文件夹', type:'text', ph:'C:/miloto/files', hint:'微信文件存放地点,如 C:\\Users\\user\\xwechat_files\\wxid\\msg\\file'},
  ]},
  {title:'媒体缓存', fields:[
    {key:'attachments', label:'媒体缓存目录（图片/文件临时落点，改后需重启）', type:'text', ph:'C:/miloto/attachments'},
  ]},
  {title:'控制台登录', fields:[
    {key:'dashboard.username', label:'控制台用户名', type:'text', ph:'miloto', hint:'修改登录密码请使用侧边栏「修改密码」'},
  ]},
];

// 配置键用的是 "web.port" 这样的分组路径，按路径逐层取值
function configValue(cfg, path) {
  var current = cfg;
  var steps = path.split('.');
  for (var i = 0; i < steps.length; i++) {
    if (current === null || typeof current !== 'object' || !(steps[i] in current)) return undefined;
    current = current[steps[i]];
  }
  return current;
}

function fieldHtml(f, cfg) {
  var val = configValue(cfg, f.key);
  if (val === undefined || val === null) val = '';
  if (Array.isArray(val)) val = val.join(', ');
  var h = '<div class="settings-field"><label>' + f.label + '</label>';
  if (f.type === 'select') {
    h += '<select id="cfg_' + f.key + '">';
    f.opts.forEach(function(o){h += '<option value="' + o.v + '"' + (val==o.v?' selected':'') + '>' + o.l + '</option>'});
    h += '</select>';
  } else if (f.type === 'number') {
    h += '<input type="number" id="cfg_' + f.key + '" value="' + val + '" placeholder="' + (f.ph||'') + '" data-def="' + (f.ph||'') + '">';
  } else {
    h += '<input type="' + f.type + '" id="cfg_' + f.key + '" value="' + String(val).replace(/"/g,'&quot;') + '" placeholder="' + (f.ph||'') + '">';
  }
  if (f.hint) h += '<div class="field-hint">' + f.hint + '</div>';
  h += '</div>';
  return h;
}
function renderForm(containerId, groups, cfg) {
  var html = '';
  groups.forEach(function(g){
    html += '<div class="settings-group"><h3>' + g.title + '</h3><div class="settings-row">';
    g.fields.forEach(function(f){ html += fieldHtml(f, cfg); });
    html += '</div></div>';
  });
  document.getElementById(containerId).innerHTML = html;
}
function loadConfig() {
  api('/api/config').then(function(r){return r.json()}).then(function(cfg){
    renderForm('basicForm', FORM_BASIC, cfg);
  }).catch(function(e){
    document.getElementById('basicForm').innerHTML = '<p style="color:#e57373;font-size:13px;">加载配置失败: ' + e.message + '</p>';
  });
}
function saveConfig(page) {
  var containerId = 'basicForm';
  var msgId = 'saveMsgBasic';
  var fields = document.querySelectorAll('#' + containerId + ' [id^="cfg_"]');
  var data = {};
  fields.forEach(function(el){
    var key = el.id.replace('cfg_','');
    var val = el.value.trim();
    if (el.type === 'number') {
      if (val === '') {
        // 数字框留空时别写 0，回退到 placeholder 上的默认值（比如端口的 8127）
        var def = el.getAttribute('data-def');
        val = (def && def !== '' && !isNaN(Number(def))) ? Number(def) : 0;
      } else {
        val = Number(val) || 0;
      }
    }
    if (key === 'bot.names') val = val ? val.split(/[,，]\\s*/).filter(Boolean) : [];
    data[key] = val;   // 键是 "web.port" 这种路径，后端会展开成嵌套结构
  });
  api('/api/config', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data),
  }).then(function(r){return r.json()}).then(function(res){
    if (res.ok) {
      if (res.restart_needed) showRestartPrompt();
      else showSave(msgId, '✅ 已保存并生效');
    } else {
      showSave(msgId, '❌ 保存失败');
    }
  }).catch(function(e){ showSave(msgId, '❌ 保存失败: ' + e.message); });
}

// ===== 连接设置（NapCat 式多连接）=====
var editingKey = null;     // 当前正在编辑的连接 "type::name"（新建用 "__new__"）
var _conn_ob = [], _conn_weflow = [];

function loadConnections() {
  api('/api/connections').then(function(r){return r.json()}).then(function(d){
    _conn_ob = d.ob || [];
    _conn_weflow = d.weflow || [];
    renderConns('ob', _conn_ob);
    renderConns('weflow', _conn_weflow);
  }).catch(function(e){
    document.getElementById('conn-ob').innerHTML = '<div class="conn-empty">加载连接失败: ' + e.message + '</div>';
  });
}
function renderConns(type, list) {
  var wrap = document.getElementById('conn-' + type);
  if (!list.length) {
    wrap.innerHTML = '<div class="conn-empty">暂无连接，点击右上角「创建」添加</div>';
    return;
  }
  var html = '';
  list.forEach(function(c){ html += connCardHtml(type, c); });
  wrap.innerHTML = html;
}
function connCardHtml(type, c) {
  var key = type + '::' + c.name;
  // 新建且未保存的卡片(_new)必须渲染成编辑表单——否则 editingKey(__new__) 与
  // key(连接名) 对不上，会错误地渲染成普通卡片，导致未保存就被开关/编辑（后端无此
  // 连接 → 操作失败 / 编辑后从服务器重载列表消失）。
  if (editingKey === key || c._new) return connEditHtml(type, c);
  var dot = c.enable ? (c.connected ? 'online' : 'offline') : 'disabled';
  var label = c.enable ? (c.connected ? '在线' : '离线') : '已停用';
    return ''+
    '<div class="conn-card" data-key="'+key+'">'+
      '<div class="conn-info"><div class="conn-name">'+esc(c.name)+'</div>'+
        '<div class="conn-url">'+esc(c.url || '(未填地址)') + (type==='ob' ? (' · 心跳 ' + (c.heartbeat!==undefined?c.heartbeat:20) + 's · 重连 ' + (c.reconnect!==undefined?c.reconnect:5) + 's') : (' · 重连 ' + (c.reconnect!==undefined?c.reconnect:10) + 's')) +'</div></div>'+
    '<div class="conn-state"><span class="dot '+dot+'"></span>'+label+'</div>'+
    '<div class="conn-actions">'+
      '<label class="switch"><input type="checkbox" '+(c.enable?'checked':'')+' onchange="toggleConn(\''+type+'\',\''+esc(c.name)+'\',this.checked)"><span class="slider"></span></label>'+
      '<button class="btn btn-outline" onclick="editConn(\''+type+'\',\''+esc(c.name)+'\')">编辑</button>'+
      '<button class="btn btn-outline" onclick="delConn(\''+type+'\',\''+esc(c.name)+'\')">删除</button>'+
    '</div>'+
  '</div>';
}
function connEditHtml(type, c) {
  var key = type + '::' + c.name;
  var isNew = !!c._new;
  var extraField = '';
  if (type === 'ob') {
    var hbVal = (c.heartbeat !== undefined && c.heartbeat !== '') ? c.heartbeat : 20;
    var rcVal = (c.reconnect !== undefined && c.reconnect !== '') ? c.reconnect : 5;
    extraField = '<div class="settings-field"><label>心跳时间（秒，WebSocket 保活）</label><input id="ed_heartbeat" type="number" min="3" max="600" value="' + hbVal + '" placeholder="20"></div>' +
                 '<div class="settings-field"><label>重连间隔（秒，断线后自动重连）</label><input id="ed_reconnect" type="number" min="1" max="600" value="' + rcVal + '" placeholder="5"></div>';
  } else {
    var rcVal = (c.reconnect !== undefined && c.reconnect !== '') ? c.reconnect : 10;
    extraField = '<div class="settings-field"><label>重连间隔（秒，WeFlow 断线/未就绪后自动重连）</label><input id="ed_reconnect" type="number" min="3" max="600" value="' + rcVal + '" placeholder="10"></div>';
  }
  return ''+
  '<div class="conn-card editing" data-key="'+key+'">'+
    '<div class="conn-edit-fields">'+
      '<div class="settings-field"><label>名称</label><input id="ed_name" value="'+esc(c.name)+'"></div>'+
      '<div class="settings-field"><label>地址</label><input id="ed_url" value="'+esc(c.url||'')+'" placeholder="'+(type==='ob'?'ws://127.0.0.1:6199/ws':'http://127.0.0.1:5031')+'"></div>'+
      '<div class="settings-field"><label>Token（留空=不鉴权）</label><input id="ed_token" type="text" value="'+esc(c.token||'')+'"></div>'+
      extraField+
    '</div>'+
    '<div class="conn-actions">'+
      '<button class="btn btn-pink" onclick="saveConn(\''+type+'\',\''+esc(c.name)+'\','+isNew+')">保存</button>'+
      '<button class="btn btn-outline" onclick="cancelEdit()">取消</button>'+
    '</div>'+
  '</div>';
}
function createConn(type) {
  editingKey = type + '::__new__';
  var c = {name:'新连接', url:'', token:'', _new:true};
  if (type === 'ob') { c.heartbeat = 20; c.reconnect = 5; }
  if (type === 'weflow') c.reconnect = 10;
  // 先清掉已有的未保存新建卡片，避免多个 __new__ 表单共用同名 id 冲突
  if (type === 'ob') { _conn_ob = _conn_ob.filter(function(x){return !x._new;}).concat([c]); renderConns('ob', _conn_ob); }
  else { _conn_weflow = _conn_weflow.filter(function(x){return !x._new;}).concat([c]); renderConns('weflow', _conn_weflow); }
}
function editConn(type, name) {
  editingKey = type + '::' + name;
  loadConnections();
}
function cancelEdit() {
  editingKey = null;
  loadConnections();
}
function saveConn(type, oldName, isNew) {
  var name = document.getElementById('ed_name').value.trim();
  var url = document.getElementById('ed_url').value.trim();
  var token = document.getElementById('ed_token').value;
  if (!name) { toast('名称不能为空', 'error'); return; }
  var body = {type:type, name:name, url:url, token:token};
  if (type === 'ob') {
    var hbEl = document.getElementById('ed_heartbeat');
    var hb = hbEl ? Number(hbEl.value) : 20;
    if (!hb || hb < 3) hb = 3;
    if (hb > 600) hb = 600;
    body.heartbeat = hb;
    var rcEl = document.getElementById('ed_reconnect');
    var rc = rcEl ? Number(rcEl.value) : 5;
    if (!rc || rc < 1) rc = 1;
    if (rc > 600) rc = 600;
    body.reconnect = rc;
  } else {
    var rcEl = document.getElementById('ed_reconnect');
    var rc = rcEl ? Number(rcEl.value) : 10;
    if (!rc || rc < 3) rc = 3;
    if (rc > 600) rc = 600;
    body.reconnect = rc;
  }
  var ops = [];
  // 改名：先删旧再建新
  if (!isNew && oldName && oldName !== name) {
    ops.push(api('/api/connections/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:type, name:oldName})}));
  }
  ops.push(api('/api/connections', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}));
  Promise.all(ops).then(function(){
    editingKey = null;
    toast('已保存', 'success');
    loadConnections();
  }).catch(function(e){ toast('保存失败: ' + e.message, 'error'); });
}
function toggleConn(type, name, enable) {
  api('/api/connections/toggle', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:type, name:name, enable:enable})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) toast(enable ? '已启用' : '已停用', 'success');
      else toast('操作失败', 'error');
    }).catch(function(e){ toast('操作失败: ' + e.message, 'error'); });
}
function delConn(type, name) {
  showModal('删除连接', '确定要删除连接「' + esc(name) + '」吗？此操作不可撤销。', [
    {label:'取消', cls:'btn-outline', onClick: hideModal},
    {label:'删除', cls:'btn-pink', onClick: function(){ hideModal(); doDelConn(type, name); }}
  ]);
}
function doDelConn(type, name) {
  api('/api/connections/delete', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:type, name:name})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) { toast('已删除', 'success'); loadConnections(); }
      else toast('删除失败', 'error');
    }).catch(function(e){ toast('删除失败: ' + e.message, 'error'); });
}

// ===== 插件管理（AstrBot 式：WebUI 直接配置插件 schema）=====
window.__pluginSchema = [];
window.__pluginListSchemas = {};

window.__pluginData = [];

function loadPlugins() {
  var grid = document.getElementById('pluginGrid');
  if (grid) grid.innerHTML = pluginSkeleton();
  api('/api/plugins').then(function(r){return r.json()}).then(function(d){
    window.__pluginData = d.plugins || [];
    renderPluginGrid();
    var c = document.getElementById('pluginCount');
    if (c) c.textContent = window.__pluginData.length;
  }).catch(function(e){
    var g = document.getElementById('pluginGrid');
    if (g) g.innerHTML = '<div class="conn-empty">加载插件失败: ' + e.message + '</div>';
  });
}

function renderPluginGrid() {
  var list = window.__pluginData || [];
  var box = document.getElementById('pluginSearch');
  var q = (box && box.value ? box.value : '').trim().toLowerCase();
  if (q) {
    list = list.filter(function(p){
      return (p.name||'').toLowerCase().indexOf(q) >= 0
          || (p.display_name||'').toLowerCase().indexOf(q) >= 0
          || (p.desc||'').toLowerCase().indexOf(q) >= 0;
    });
  }
  var grid = document.getElementById('pluginGrid');
  if (!list.length) {
    grid.innerHTML = q
      ? '<div class="conn-empty">没有匹配「' + esc(q) + '」的插件</div>'
      : '<div class="conn-empty">暂无已安装插件</div>';
    return;
  }
  grid.innerHTML = list.map(pluginCardHtml).join('');
}

function pluginSubTab(tab) {
  document.querySelectorAll('.ptab').forEach(function(b){
    b.classList.toggle('active', b.getAttribute('data-ptab') === tab);
  });
  document.getElementById('pluginInstalled').style.display = (tab === 'installed') ? '' : 'none';
  document.getElementById('pluginMarket').style.display = (tab === 'market') ? '' : 'none';
  if (tab === 'market') loadMarket();
}

function pluginSkeleton() {
  var s = '';
  for (var i = 0; i < 6; i++) {
    s += '<div class="plugin-card skel">'+
      '<div class="pc-head"><div class="pc-icon sk"></div>'+
      '<div class="pc-title"><div class="sk-line w60"></div><div class="sk-line w40"></div></div></div>'+
      '<div class="sk-line w90"></div><div class="sk-line w70"></div></div>';
  }
  return s;
}

function pluginCardHtml(p) {
  var initials = (p.display_name || p.name || '?').slice(0, 2).toUpperCase();
  var stateCls = p.enabled ? 'online' : 'offline';
  var stateTxt = p.enabled ? '已启用' : '已停用';
  return ''+
  '<div class="plugin-card">'+
    '<div class="pc-head">'+
      '<div class="pc-icon">'+esc(initials)+'</div>'+
      '<div class="pc-title">'+
        '<div class="pc-name">'+esc(p.display_name || p.name)+
          ' <span class="plugin-ver">v'+esc(p.version || '?')+'</span></div>'+
        '<div class="pc-author">'+(p.author ? ('作者 '+esc(p.author)) : '本地插件')+'</div>'+
      '</div>'+
      '<label class="switch"><input type="checkbox" '+(p.enabled?'checked':'')+
        ' onchange="togglePlugin(\''+esc(p.name)+'\',this.checked)"><span class="slider"></span></label>'+
    '</div>'+
    '<div class="pc-desc">'+esc(p.desc || '(无描述)')+'</div>'+
    '<div class="pc-foot">'+
      '<span class="pc-status"><span class="dot '+stateCls+'"></span>'+stateTxt+'</span>'+
      '<div class="pc-actions">'+
        '<button class="btn btn-ghost" onclick="reloadPlugin(\''+esc(p.name)+'\')">重载</button>'+
        '<button class="btn btn-outline" onclick="openPluginConfig(\''+esc(p.name)+'\')">配置</button>'+
      '</div>'+
    '</div>'+
  '</div>';
}
function togglePlugin(name, enable) {
  api('/api/plugins/toggle', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name, enable:enable})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) {
        toast(enable ? '已启用' : '已停用', 'success');
        loadPlugins();  // 实时刷新卡片状态（已启用/已停用 + 圆点）
      } else {
        toast('操作失败', 'error');
        loadPlugins();  // 还原开关（失败后回退到服务端真实状态）
      }
    }).catch(function(e){
      toast('操作失败: ' + e.message, 'error');
      loadPlugins();  // 还原开关
    });
}
function openPluginConfig(name) {
  api('/api/plugins/config?name=' + encodeURIComponent(name))
    .then(function(r){return r.json()}).then(function(res){
      if (res.error) { toast('加载失败: ' + res.error, 'error'); return; }
      renderPluginConfigModal(name, res.schema, res.config);
    }).catch(function(e){ toast('加载失败: ' + e.message, 'error'); });
}
function reloadPlugin(name) {
  toast('正在重载 ' + name + '…', 'success');
  api('/api/plugins/reload', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) { toast('已重载 ' + name, 'success'); loadPlugins(); }
      else toast('重载失败', 'error');
    }).catch(function(e){ toast('重载失败: ' + e.message, 'error'); });
}

// ===== 插件市场（AstrBot 风：远程索引 + 一键安装）=====
window.__marketData = [];
function loadMarket() {
  var grid = document.getElementById('marketGrid');
  if (grid) grid.innerHTML = pluginSkeleton();
  api('/api/plugins/market').then(function(r){return r.json()}).then(function(d){
    if (d.status && d.status !== 'ok') {
      renderMarketState(d.status);
      return;
    }
    window.__marketData = d.plugins || [];
    renderMarketGrid();
  }).catch(function(e){
    var g = document.getElementById('marketGrid');
    if (g) g.innerHTML = '<div class="conn-empty">加载市场失败: ' + e.message + '</div>';
  });
}
function renderMarketState(status) {
  var g = document.getElementById('marketGrid');
  if (!g) return;
  var msg, sub;
  if (status === 'market_not_configured') {
    msg = '插件市场未配置';
    sub = '在「基础设置」里填写 plugins.market_url（指向 market.json 索引的地址）后即可使用。';
  } else if (status === 'market_unreachable') {
    msg = '无法连接插件市场';
    sub = '请检查网络后重试；插件需要联网下载，离线时市场不可用。';
  } else {
    msg = '插件市场暂时不可用';
    sub = '';
  }
  g.innerHTML = '<div class="conn-empty"><div class="ce-title">' + esc(msg) + '</div>' +
    (sub ? '<div class="ce-sub">' + esc(sub) + '</div>' : '') + '</div>';
}
function renderMarketGrid() {
  var list = window.__marketData || [];
  var box = document.getElementById('pluginSearch');
  var q = (box && box.value ? box.value : '').trim().toLowerCase();
  if (q) {
    list = list.filter(function(p){
      return (p.name||'').toLowerCase().indexOf(q) >= 0
          || (p.display_name||'').toLowerCase().indexOf(q) >= 0
          || (p.desc||'').toLowerCase().indexOf(q) >= 0;
    });
  }
  var grid = document.getElementById('marketGrid');
  if (!grid) return;
  if (!list.length) {
    grid.innerHTML = q
      ? '<div class="conn-empty">没有匹配「' + esc(q) + '」的插件</div>'
      : '<div class="conn-empty">市场暂无插件</div>';
    return;
  }
  grid.innerHTML = list.map(marketCardHtml).join('');
}
function marketCardHtml(p) {
  var initials = (p.name || '?').slice(0, 2).toLowerCase();
  var action;
  if (p.installed && !p.updatable) {
    action = '<button class="btn btn-ghost" disabled>已安装</button>';
  } else if (p.installed && p.updatable) {
    action = '<button class="btn btn-outline" onclick="installPlugin(\''+esc(p.name)+'\', true)">更新</button>';
  } else {
    action = '<button class="btn btn-pink" onclick="installPlugin(\''+esc(p.name)+'\', false)">安装</button>';
  }
  var repo = p.repo ? '<a class="pc-gh" href="'+esc(p.repo)+'" target="_blank" rel="noopener" title="在 GitHub 查看源码"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0 0 16 8c0-4.42-3.58-8-8-8z"></path></svg></a>' : '';
  return ''+
  '<div class="plugin-card">'+
    '<div class="pc-head">'+
      '<div class="pc-icon">'+esc(initials)+'</div>'+
      '<div class="pc-title">'+
        '<div class="pc-name">'+esc(p.name)+
          ' <span class="plugin-ver">v'+esc(p.version || '?')+'</span>'+
          '</div>'+
        '<div class="pc-author">'+(p.author ? ('作者 '+esc(p.author)) : '社区插件')+'</div>'+
      '</div>'+
    '</div>'+
    '<div class="pc-desc">'+esc(p.desc || '(无描述)')+'</div>'+
    '<div class="pc-foot">'+
      '<span class="pc-status">'+(p.installed ? ('已安装 v'+esc(p.installed_version||'')) : '未安装')+'</span>'+
      '<div class="pc-actions">'+ repo + action +'</div>'+
    '</div>'+
  '</div>';
}
function installPlugin(name, update) {
  toast((update ? '正在更新 ' : '正在安装 ') + name + '…', 'success');
  api('/api/plugins/install', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) {
        toast(update ? ('已更新 '+name) : ('已安装 '+name), 'success');
        loadMarket(); loadPlugins();
      } else {
        toast('失败: ' + (res.error || '未知错误'), 'error');
      }
    }).catch(function(e){ toast('失败: ' + e.message, 'error'); });
}
function renderPluginConfigModal(name, schema, config) {
  window.__pluginSchema = schema || [];
  window.__pluginListSchemas = {};
  var html = '';
  if (!schema || !schema.length) {
    html = '<div class="conn-empty">该插件暂无可配置项</div>';
  } else {
    schema.forEach(function(f){
      if (f.type === 'list') window.__pluginListSchemas[f.key] = f;
      html += pluginFieldHtml(f, config || {});
    });
  }
  showModal('配置 · ' + esc(name), '<div class="plugin-form">' + html + '</div>', [
    {label:'取消', cls:'btn-outline', onClick: hideModal},
    {label:'保存', cls:'btn-pink', onClick: function(){ savePluginConfig(name); }}
  ]);
}
function pVal(cfg, key, def) {
  return (cfg && cfg[key] !== undefined) ? cfg[key] : def;
}
function pluginFieldHtml(f, cfg) {
  if (f.type === 'bool') {
    var b = pVal(cfg, f.key, false);
    return '<div class="settings-field"><label class="switch-line"><span>'+esc(f.label)+'</span>'+
      '<label class="switch"><input type="checkbox" data-pkey="'+esc(f.key)+'" data-ptype="bool" '+
      (b?'checked':'')+'><span class="slider"></span></label></label>'+
      (f.hint ? '<div class="field-hint">'+f.hint+'</div>' : '')+'</div>';
  }
  if (f.type === 'list') {
    var items = pVal(cfg, f.key, []);
    if (!Array.isArray(items)) items = [];
    var rows = items.map(function(it, i){ return pluginItemHtml(f, it, i); }).join('');
    return '<div class="settings-field"><label>'+esc(f.label)+'</label>'+
      (f.hint ? '<div class="field-hint">'+f.hint+'</div>' : '')+
      '<div class="plist" data-pkey="'+esc(f.key)+'">'+rows+
      '<button type="button" class="btn btn-outline" onclick="pluginAddItem(this)">+ 添加一项</button>'+
      '</div></div>';
  }
  var val = pVal(cfg, f.key, '');
  var tag;
  if (f.type === 'select') {
    var opts = (f.opts || []).map(function(o){
      return '<option value="'+esc(o.v)+'"'+(val==o.v?' selected':'')+'>'+esc(o.l)+'</option>';
    }).join('');
    tag = '<select data-pkey="'+esc(f.key)+'" data-ptype="select">'+opts+'</select>';
  } else if (f.type === 'int' || f.type === 'float' || f.type === 'number') {
    tag = '<input type="number" data-pkey="'+esc(f.key)+'" data-ptype="number" value="'+
      (val===0||val?val:'')+'" placeholder="'+(f.ph||'')+'">';
  } else {
    tag = '<input type="text" data-pkey="'+esc(f.key)+'" data-ptype="text" value="'+
      String(val).replace(/"/g,'&quot;')+'" placeholder="'+(f.ph||'')+'">';
  }
  return '<div class="settings-field"><label>'+esc(f.label)+'</label>'+tag+
    (f.hint ? '<div class="field-hint">'+f.hint+'</div>' : '')+'</div>';
}
function pluginItemHtml(f, it, idx) {
  var schema = f.item_schema || [];
  var cells = schema.map(function(sf){
    var v = it ? it[sf.key] : '';
    if (sf.type === 'select') {
      var opts = (sf.opts||[]).map(function(o){
        return '<option value="'+esc(o.v)+'"'+(v==o.v?' selected':'')+'>'+esc(o.l)+'</option>';
      }).join('');
      return '<select data-ikey="'+esc(sf.key)+'">'+opts+'</select>';
    }
    return '<input type="text" data-ikey="'+esc(sf.key)+'" value="'+
      String(v==null?'':v).replace(/"/g,'&quot;')+'" placeholder="'+(sf.ph||'')+'">';
  }).join('');
  return '<div class="pitem">'+cells+
    '<button type="button" class="btn btn-outline pitem-del" onclick="pluginDelItem(this)">✕</button></div>';
}
function pluginAddItem(btn) {
  var list = btn.parentNode;
  var key = list.getAttribute('data-pkey');
  var f = window.__pluginListSchemas[key];
  if (!f) return;
  var wrap = document.createElement('div');
  wrap.innerHTML = pluginItemHtml(f, null, 0).trim();
  list.insertBefore(wrap.firstChild, btn);
}
function pluginDelItem(btn) {
  btn.parentNode.remove();
}
function savePluginConfig(name) {
  var cfg = {};
  // 标量 / 布尔 / 下拉
  document.querySelectorAll('#modalBody [data-pkey]').forEach(function(el){
    if (el.classList.contains('plist')) return;   // 列表单独处理
    var key = el.getAttribute('data-pkey');
    var type = el.getAttribute('data-ptype');
    var v;
    if (type === 'bool') v = el.checked;
    else if (type === 'number') v = (el.value === '' ? 0 : Number(el.value));
    else v = el.value;
    cfg[key] = v;
  });
  // 列表（list）
  document.querySelectorAll('#modalBody .plist').forEach(function(listEl){
    var key = listEl.getAttribute('data-pkey');
    var f = window.__pluginListSchemas[key] || {};
    var item_schema = f.item_schema || [];
    var items = [];
    listEl.querySelectorAll('.pitem').forEach(function(itemEl){
      var row = {};
      itemEl.querySelectorAll('[data-ikey]').forEach(function(iel){
        var ik = iel.getAttribute('data-ikey');
        var sf = item_schema.find(function(x){ return x.key === ik; });
        var v = iel.value;
        if (sf && (sf.type === 'int' || sf.type === 'float' || sf.type === 'number')) v = (v==='' ? 0 : Number(v));
        row[ik] = v;
      });
      items.push(row);
    });
    cfg[key] = items;
  });
  api('/api/plugins/config', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name, config:cfg})})
    .then(function(r){return r.json()}).then(function(res){
      if (res.ok) { toast('已保存', 'success'); hideModal(); loadPlugins(); }
      else toast('保存失败', 'error');
    }).catch(function(e){ toast('保存失败: ' + e.message, 'error'); });
}

// ===== 重启询问弹窗 =====
function showRestartPrompt() {
  showModal('需要重启程序',
    '媒体缓存目录（或 Web 监听地址）已变更，需重启程序后才能完全生效。是否立即重启？',
    [
      {label:'稍后重启', cls:'btn-outline', onClick: hideModal},
      {label:'立即重启', cls:'btn-pink', onClick: function(){ hideModal(); doRestart(); }}
    ]);
}
function doRestart() {
  toast('正在重启程序…', 'success');
  api('/api/restart', {method:'POST'}).catch(function(){}).finally(function(){
    // 轮询新进程是否就绪，就绪后自动刷新控制台
    var tries = 0;
    var tryReload = function(){
      tries++;
      api('/status').then(function(){ location.reload(); })
        .catch(function(){ if (tries < 20) setTimeout(tryReload, 1000); else location.reload(); });
    };
    setTimeout(tryReload, 2500);
  });
}

// ===== 初始化 =====
var _appStarted = false;
function startAppLoop() {
  // 避免重复启动轮询（登出再登录时仍复用已有定时器）
  if (_appStarted) return;
  _appStarted = true;
  refresh();
  setInterval(refresh, 3000);
}
function bootApp() {
  // 已登录则判断是否需强制改密；否则显示登录层（遮罩默认可见）
  if (!getToken()) { showLogin(); return; }
  // 用原生 fetch 探测（避免 api() 的 401 递归弹登录）
  fetch('/api/auth-state').then(function(r){return r.json()}).then(function(s){
    if (s.must_change_pw) { showForceChange(); return; }
    var m = document.getElementById('loginMask');
    if (m) m.style.display = 'none';
    startAppLoop();
  }).catch(function(){
    var m = document.getElementById('loginMask');
    if (m) m.style.display = 'none';
    startAppLoop();
  });
}
bootApp();
</script>
</body>
</html>"""

class WebHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        if self.path == "/api/auth-state":
            self._serve_auth_state()
            return
        if self.path.startswith("/api/") or self.path == "/status":
            if not self._require_auth():
                return
        if self.path == "/status":
            self._serve_status()
        elif self.path == "/api/config":
            self._serve_config()
        elif self.path == "/api/connections":
            self._serve_connections()
        elif self.path.startswith("/api/plugins/config"):
            self._serve_plugin_config_get()
        elif self.path == "/api/plugins":
            self._serve_plugins()
        elif self.path == "/api/plugins/market":
            self._serve_plugins_market()
        else:

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(PAGE.encode("utf-8"))

    def do_POST(self):

        if self.path != "/api/login":
            if not self._require_auth():
                return
        if self.path == "/api/login":
            self._serve_login()
        elif self.path == "/api/setup":
            self._serve_setup()
        elif self.path == "/start":
            _ENGINE.start()
            self.send_json({"ok": True})
        elif self.path == "/stop":
            _ENGINE.stop()
            self.send_json({"ok": True})
        elif self.path == "/pause":
            _ENGINE.pause()
            self.send_json({"ok": True})
        elif self.path == "/resume":
            _ENGINE.resume()
            self.send_json({"ok": True})
        elif self.path == "/api/config":
            self._serve_save_config()
        elif self.path == "/api/loglevel":
            self._serve_loglevel()
        elif self.path == "/api/connections":
            self._serve_upsert_connection()
        elif self.path == "/api/connections/toggle":
            self._serve_toggle_connection()
        elif self.path == "/api/connections/delete":
            self._serve_delete_connection()
        elif self.path == "/api/plugins/toggle":
            self._serve_toggle_plugin()
        elif self.path == "/api/plugins/config":
            self._serve_save_plugin_config()
        elif self.path == "/api/plugins/reload":
            self._serve_reload_plugin()
        elif self.path == "/api/plugins/install":
            self._serve_install_plugin()
        elif self.path == "/api/plugins/uninstall":
            self._serve_uninstall_plugin()
        elif self.path == "/api/change-password":
            self._serve_change_password()
        elif self.path == "/api/restart":
            self._serve_restart()

        else:
            self.send_json({"ok": False}, 404)

    def _require_auth(self) -> bool:

        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else auth
        if token and token in _SESSIONS:
            return True
        self.send_json({"ok": False, "error": "未授权，请先登录", "code": 401}, 401)
        return False

    def _client_ip(self):
        try:
            return self.client_address[0]
        except Exception:
            return "?"

    def _login_locked(self, ip):

        rec = _LOGIN_FAILS.get(ip)
        if not rec:
            return 0
        fails, last = rec
        left = int(_LOGIN_LOCK_SECS - (time.time() - last))
        if fails < _LOGIN_MAX_FAILS or left <= 0:
            if left <= 0:
                _LOGIN_FAILS.pop(ip, None)
            return 0
        return left

    def _serve_login(self):

        ip = self._client_ip()
        left = self._login_locked(ip)
        if left:
            self.send_json({"ok": False, "error": f"登录失败次数过多，请 {left} 秒后再试"}, 429)
            return
        try:
            body = self._read_body()
            u = str(body.get("username", ""))
            p = str(body.get("password", ""))
            ok_user = hmac.compare_digest(u, _DASHBOARD["username"])
            ok_pass = verify_password(p, _DASHBOARD["password_hash"])
            if ok_user and ok_pass:
                _LOGIN_FAILS.pop(ip, None)

                if not _DASHBOARD.get("is_initial") and needs_upgrade(_DASHBOARD["password_hash"]):
                    try:
                        save_new_password(p)
                        log.info("[Web] 控制台密码已自动升级为哈希存储")
                    except Exception as e:
                        log.warning(f"[Web] 密码哈希升级失败（不影响登录）: {e}")
                token = secrets.token_hex(16)
                _SESSIONS[token] = u
                self.send_json({"ok": True, "token": token,
                                "must_change_pw": bool(_DASHBOARD.get("is_initial"))})
            else:
                rec = _LOGIN_FAILS.get(ip, [0, 0])
                _LOGIN_FAILS[ip] = [rec[0] + 1, time.time()]
                log.warning(f"[Web] 登录失败（{ip}），累计 {rec[0] + 1} 次")
                self.send_json({"ok": False, "error": "用户名或密码错误"}, 401)
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_auth_state(self):

        self.send_json({
            "must_change_pw": bool(_DASHBOARD.get("is_initial")),
            "username": _DASHBOARD["username"],
        })

    def _serve_setup(self):

        if not _DASHBOARD.get("is_initial"):
            self.send_json({"ok": False, "error": "非首次设置，请使用侧栏「修改密码」"}, 400)
            return
        try:
            body = self._read_body()
            new = str(body.get("password", ""))
            if len(new) < _PASSWORD_MIN_LEN:
                self.send_json({"ok": False, "error": f"密码至少 {_PASSWORD_MIN_LEN} 位"}, 400)
                return

            save_new_password(new)
            log.info("[Web] 首次设置控制台密码完成")
            self.send_json({"ok": True})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_change_password(self):

        try:
            body = self._read_body()
            old = str(body.get("old_password", ""))
            new = str(body.get("new_password", ""))
            if not verify_password(old, _DASHBOARD["password_hash"]):
                self.send_json({"ok": False, "error": "当前密码不正确"}, 400)
                return
            if len(new) < _PASSWORD_MIN_LEN:
                self.send_json({"ok": False, "error": f"新密码至少 {_PASSWORD_MIN_LEN} 位"}, 400)
                return

            save_new_password(new)
            _SESSIONS.clear()
            log.info("[Web] 控制台密码已修改，所有会话已失效")
            self.send_json({"ok": True})
        except Exception as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_status(self):

        st = _ENGINE.status()

        st["log"] = "\n".join(get_log_lines(200))
        st["log_levels"] = _ENGINE.settings.log_levels
        self.send_json(st)

    def _serve_config(self):

        try:
            config = _ENGINE.get_config_dict()
            config.pop("dashboard_password", None)
            dashboard = config.get("dashboard")
            if isinstance(dashboard, dict):
                config["dashboard"] = {k: v for k, v in dashboard.items()
                                       if k != "password"}
            self.send_json(config)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def _serve_loglevel(self):

        try:
            body = self._read_body()
            level = str(body.get("level", "")).lower()
            on = bool(body.get("on", False))
            if level not in ("debug", "info", "warning", "error"):
                self.send_json({"ok": False, "error": "未知级别: " + level}, 400)
                return
            _ENGINE.set_log_level(level, on)
            self.send_json({"ok": True, "level": level, "on": on})
        except Exception as e:
            log.error(f"[Web] 设置日志级别异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_connections(self):

        status = _ENGINE.status()
        self.send_json({"ob": status["onebot_connections"],
                        "weflow": status["weflow_connections"]})

    def _serve_plugins(self):

        try:
            self.send_json({"plugins": _ENGINE.list_plugins()})
        except Exception as e:
            log.error(f"[Web] 读取插件列表异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _load_market_index(self):

        url = (_ENGINE.settings.plugin_market_url or "").strip()
        if not url:
            return [], "market_not_configured"
        try:
            import urllib.request
            if url.startswith(("http://", "https://")):
                req = urllib.request.Request(url, headers={"User-Agent": "Miloto"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read().decode("utf-8"))
            else:

                with open(url, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            return data.get("plugins", []) or [], None
        except Exception as exc:
            log.warning(f"[Web] 插件市场索引拉取失败: {exc}")
            return [], "market_unreachable"

    def _version_gt(self, a, b):

        def t(v):
            import re
            return tuple(int(x) for x in re.findall(r"\d+", v or ""))
        return t(a) > t(b)

    def _serve_plugins_market(self):

        try:
            market, status = self._load_market_index()
            if status:
                self.send_json({"status": status, "plugins": []})
                return
            installed = {p["name"]: p for p in _ENGINE.list_plugins()}
            out = []
            for item in market:
                name = item.get("name", "")
                inst = installed.get(name)
                entry = dict(item)
                entry["installed"] = inst is not None
                entry["installed_version"] = inst.get("version", "") if inst else ""
                entry["updatable"] = bool(inst) and self._version_gt(
                    item.get("version", ""), inst.get("version", ""))
                out.append(entry)
            self.send_json({"status": "ok", "plugins": out})
        except Exception as e:
            log.error(f"[Web] 读取市场异常: {e}")
            self.send_json({"status": "market_unreachable", "plugins": [], "error": str(e)}, 500)

    def _serve_install_plugin(self):

        try:
            body = self._read_body()
            name = str(body.get("name", ""))
            if not name:
                self.send_json({"ok": False, "error": "缺少 name 参数"}, 400)
                return
            market, status = self._load_market_index()
            if status:
                self.send_json({"ok": False, "error": "插件市场当前不可用，无法安装"}, 409)
                return
            source = market.get(name)
            if not source:
                self.send_json({"ok": False, "error": "市场里找不到该插件"}, 404)
                return
            err = _ENGINE.install_plugin(name, source)
            if err:
                self.send_json({"ok": False, "error": err}, 500)
            else:
                self.send_json({"ok": True})
        except Exception as e:
            log.error(f"[Web] 安装插件异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_uninstall_plugin(self):

        try:
            body = self._read_body()
            name = str(body.get("name", ""))
            if not name:
                self.send_json({"ok": False, "error": "缺少 name 参数"}, 400)
                return
            err = _ENGINE.uninstall_plugin(name)
            if err:
                self.send_json({"ok": False, "error": err}, 500)
            else:
                self.send_json({"ok": True})
        except Exception as e:
            log.error(f"[Web] 卸载插件异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_plugin_config_get(self):

        try:
            qs = parse_qs(urlparse(self.path).query)
            name = (qs.get("name") or [""])[0]
            if not name:
                self.send_json({"ok": False, "error": "缺少 name 参数"}, 400)
                return
            self.send_json(_ENGINE.get_plugin_config(name))
        except Exception as e:
            log.error(f"[Web] 读取插件配置异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_toggle_plugin(self):

        try:
            body = self._read_body()
            name = str(body.get("name", ""))
            if not name:
                self.send_json({"ok": False, "error": "缺少 name 参数"}, 400)
                return
            _ENGINE.set_plugin_enabled(name, bool(body.get("enable", False)))
            self.send_json({"ok": True})
        except Exception as e:
            log.error(f"[Web] 开关插件异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_save_plugin_config(self):

        try:
            body = self._read_body()
            name = str(body.get("name", ""))
            if not name:
                self.send_json({"ok": False, "error": "缺少 name 参数"}, 400)
                return
            cfg = body.get("config", {}) or {}
            _ENGINE.save_plugin_config(name, cfg)
            self.send_json({"ok": True})
        except Exception as e:
            log.error(f"[Web] 保存插件配置异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_reload_plugin(self):

        try:
            body = self._read_body()
            name = str(body.get("name", ""))
            if not name:
                self.send_json({"ok": False, "error": "缺少 name 参数"}, 400)
                return
            ok = _ENGINE.reload_plugin(name)
            self.send_json({"ok": ok})
        except Exception as e:
            log.error(f"[Web] 重载插件异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        return json.loads(raw or "{}")

    def _serve_save_config(self):

        try:
            new_config = self._read_body()

            def restart_sensitive():
                s = _ENGINE.settings
                return (s.web_host, s.web_port, s.attachments)

            before = restart_sensitive()
            _ENGINE.apply_config(new_config)
            restart_needed = restart_sensitive() != before
            if restart_needed:
                log.info("[Web] 监听地址 / 端口 / 媒体缓存目录已改动，重启后生效")

            refresh_dashboard_creds()
            self.send_json({"ok": True, "restart_needed": restart_needed})
        except Exception as e:
            log.error(f"[Web] 保存配置异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_upsert_connection(self):

        try:
            body = self._read_body()
            ok, msg = _ENGINE.upsert_connection(body.get("type", "ob"), body)
            self.send_json({"ok": ok, "error": (None if ok else msg)})
        except Exception as e:
            log.error(f"[Web] 保存连接异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_toggle_connection(self):

        try:
            body = self._read_body()
            ok = _ENGINE.set_connection_enabled(
                body.get("type", "ob"), body.get("name", ""), bool(body.get("enable", False)))
            self.send_json({"ok": ok})
        except Exception as e:
            log.error(f"[Web] 开关连接异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_delete_connection(self):

        try:
            body = self._read_body()
            ok = _ENGINE.delete_connection(body.get("type", "ob"), body.get("name", ""))
            self.send_json({"ok": ok})
        except Exception as e:
            log.error(f"[Web] 删除连接异常: {e}")
            self.send_json({"ok": False, "error": str(e)}, 500)

    def _serve_restart(self):

        self.send_json({"ok": True})
        threading.Thread(target=self._delayed_restart, daemon=True).start()

    def _delayed_restart(self):

        log.info("[Web] 用户请求重启程序")
        time.sleep(1.2)
        try:
            _ENGINE.stop()
        except Exception:
            pass
        try:
            srv = getattr(_ENGINE, "web_server", None)
            if srv is not None:
                srv.shutdown()
                srv.server_close()
        except Exception:
            pass
        python = sys.executable
        os.execv(python, [python] + sys.argv)

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")

        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        if origin:
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass

def start_web(engine) -> HTTPServer:

    global _ENGINE
    _ENGINE = engine
    init_dashboard_creds()
    host = engine.settings.web_host
    port = engine.settings.web_port

    ensure_port_free(host, port, "网页控制台")
    server = HTTPServer((host, port), WebHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    engine.web_server = server
    print_startup_banner(host, port)
    return server
