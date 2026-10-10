#!/usr/bin/env python3
"""Publish only the integrity-checked release payload to a NEW PRIVATE GitHub repo.
No business files, preexisting git history, or local reports are copied.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def prepare(root: Path, staging: Path) -> Path:
    manifest=json.loads((root/'PUBLICATION_MANIFEST.json').read_text(encoding='utf-8'))
    entries=manifest.get('files',{})
    if not entries:
        raise ValueError('发布清单为空。')
    checked=[]
    for relative, expected in entries.items():
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
            raise ValueError('清单路径无效。')
        source=root/path
        if source.is_symlink() or root.resolve() not in source.resolve().parents:
            raise ValueError('清单包含外部路径或符号链接。')
        payload=source.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=expected:
            raise ValueError('交付文件已被修改。先人工审核再更新发布清单，拒绝自动上传。')
        checked.append((path,payload))
    staging.mkdir(parents=True,exist_ok=False)
    for path,payload in checked:
        destination=staging/path
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(payload)
    shutil.copyfile(root/'PUBLICATION_MANIFEST.json',staging/'PUBLICATION_MANIFEST.json')
    return staging


def run(*args, cwd=None, capture=False):
    return subprocess.run(args,cwd=cwd,check=True,text=True,capture_output=capture)


def main(argv=None):
    p=argparse.ArgumentParser(description='首次发布至新的私有GitHub仓库，不覆盖已有仓库')
    p.add_argument('--repo',default='dashboard-data-auditor',help='当前认证用户下的新仓库名，不接受组织或现有仓库')
    p.add_argument('--prepare-only',type=Path,help='仅将经过校验的发布内容准备至新目录，不联网')
    args=p.parse_args(argv)
    os.environ["GH_HOST"]="github.com"
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',args.repo):
        p.error('仓库名无效。')
    try:
        if args.prepare_only:
            prepare(ROOT,args.prepare_only)
            print('发布内容已离线校验并准备；未创建GitHub仓库。')
            return 0
        # Stop before authentication if required local software is missing.
        if not shutil.which('gh'):
            print('未发布：本运行环境未安装GitHub CLI。安装后重新运行；Mac且已安装Homebrew可执行 brew install gh。',file=sys.stderr)
            return 3
        if not shutil.which('git'):
            print('未发布：本运行环境未安装git。',file=sys.stderr)
            return 3
        temp=Path(tempfile.mkdtemp(prefix='dashboard-skill-publish-'))
        stage=prepare(ROOT,temp/'payload')
        run(sys.executable,'-m','unittest','discover','-s','tests',cwd=stage)
        auth=subprocess.run(['gh','auth','status','--hostname','github.com'],capture_output=True,text=True)
        if auth.returncode:
            run('gh','auth','login','--hostname','github.com','--git-protocol','https','--web')
        user=json.loads(run('gh','api','--hostname','github.com','user',capture=True).stdout)
        login=user['login']
        if not re.fullmatch(r'[A-Za-z0-9-]+',login) or not isinstance(user.get('id'),int):
            raise ValueError('无法验证GitHub认证身份。')
        full=f'{login}/{args.repo}'
        existing=subprocess.run(['gh','repo','view',full,'--json','name'],capture_output=True,text=True)
        if existing.returncode==0:
            raise ValueError('目标仓库已存在。为防覆盖，程序已停止；使用--repo指定另一个新名字。')
        run('git','init','-b','main',cwd=stage)
        run('git','add','--all',cwd=stage) # Fresh directory contains ONLY the verified manifest payload.
        run('git','-c',f'user.name={login}','-c',f'user.email={user["id"]}+{login}@users.noreply.github.com',
            'commit','-m','Initial dashboard data auditor skill v0.1.0',cwd=stage)
        local=run('git','rev-parse','HEAD',cwd=stage,capture=True).stdout.strip()
        run('gh','repo','create',full,'--private','--source',str(stage),'--remote','origin','--push',
            '--description','经营看板底数核对Skill：口径门禁、差异证据、利润复算与脱敏。合成示例，无业务数据。',cwd=stage)
        remote=run('gh','api','--hostname','github.com',f'repos/{full}/commits/main','--jq','.sha',capture=True).stdout.strip()
        info=json.loads(run('gh','repo','view',full,'--json','url,isPrivate',capture=True).stdout)
        if remote!=local or info.get('isPrivate') is not True:
            raise ValueError('仓库或提交状态与预期不符；不能报告发布成功，请检查远端。')
        print(f'已发布并核对远端提交（私有）：{info["url"]}\ncommit: {remote}')
        return 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.CalledProcessError) as exc:
        print('发布未完成：'+(str(exc) if isinstance(exc,ValueError) else '文件、命令、认证或网络检查失败。'),file=sys.stderr)
        return 3

if __name__=='__main__':
    raise SystemExit(main())
