"""
夸克网盘文件读取模块

从夸克网盘下载/读取文件内容，存储为知识节点到 knowX 知识图谱。
支持文件类型：.md, .txt, .py, .json, .csv, .jpg, .png, .pdf
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import sqlite3

from quark_client.config import get_config_dir as quark_get_config_dir
from quark_client.core.api_client import QuarkAPIClient


class QuarkReader:
    """夸克网盘文件读取器"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化读取器

        Args:
            config_path: config.json 路径，默认为项目根目录下的 config.json
        """
        if config_path:
            with open(config_path) as f:
                self.project_config = json.load(f)
        else:
            # 自动查找 config.json
            config_path = self._find_config()
            if not config_path:
                raise FileNotFoundError("未找到 config.json，请指定 config_path")
            with open(config_path) as f:
                self.project_config = json.load(f)

        self.db_path = os.path.join(os.path.dirname(config_path), self.project_config.get("graph_db", "data/graph.db"))
        self.quark_cookies_path = os.path.join(os.path.dirname(config_path), "config", "cookies.json")
        self._client = None

    def _find_config(self) -> Optional[str]:
        """在项目目录中查找 config.json"""
        # 从当前工作目录或项目根目录查找
        candidates = [
            "config.json",
            os.path.join(os.getcwd(), "config.json"),
            os.path.join(os.path.dirname(__file__), "..", "config.json"),
        ]
        for path in candidates:
            path = os.path.normpath(path)
            if os.path.exists(path):
                return path
        return None

    def _get_api_client(self) -> QuarkAPIClient:
        """获取夸克 API 客户端"""
        if self._client is None:
            cookies_path = self.quark_cookies_path
            if not os.path.exists(cookies_path):
                raise FileNotFoundError(f"夸克 cookies 文件不存在: {cookies_path}\n请先运行: quarkpan auth login --method simple")

            with open(cookies_path) as f:
                cookie_data = json.load(f)
                cookie_list = cookie_data.get("cookies", [])
                cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookie_list])

            self._client = QuarkAPIClient(auto_login=False)
            self._client.cookies = cookie_str

        return self._client

    def _resolve_path_to_fid(self, path: str) -> Optional[str]:
        """
        将路径解析为夸克网盘文件夹 ID

        Args:
            path: 夸克网盘中的路径，如 "学习/39 N8N AI 自动化大师课"

        Returns:
            文件夹 fid 或 None
        """
        client = self._get_api_client()

        # 解析路径
        path_parts = [p.strip() for p in path.split("/") if p.strip()]

        current_fid = "0"  # 从根目录开始

        for part in path_parts:
            resp = client.get("/file/sort", params={
                "pdir_fid": current_fid,
                "_page": "1",
                "_size": "200",
            })

            items = resp.get("data", {}).get("list", [])
            found = False
            for item in items:
                if item.get("file_name") == part:
                    if item.get("dir"):
                        current_fid = item.get("fid")
                        found = True
                        break
                    else:
                        # 返回文件的 fid
                        return item.get("fid"), item.get("file_name")
                        found = True
                        break

            if not found:
                print(f"未找到路径段: {part} (在 {current_fid} 下)")
                return None

        return current_fid

    def list_folder(self, path: str) -> List[Dict[str, Any]]:
        """
        列出夸克网盘文件夹内容

        Args:
            path: 文件夹路径

        Returns:
            文件列表
        """
        client = self._get_api_client()
        fid = self._resolve_path_to_fid(path)
        if not fid or isinstance(fid, tuple):
            return []

        resp = client.get("/file/sort", params={
            "pdir_fid": fid,
            "_page": "1",
            "_size": "200",
        })

        items = resp.get("data", {}).get("list", [])
        result = []
        for item in items:
            result.append({
                "name": item.get("file_name", ""),
                "fid": item.get("fid"),
                "is_folder": item.get("dir", False),
                "size": item.get("size", 0),
            })
        return result

    def get_file_content(self, path: str) -> Optional[str]:
        """
        获取文本文件内容

        Args:
            path: 文件路径

        Returns:
            文件内容字符串，失败返回 None
        """
        client = self._get_api_client()
        result = self._resolve_path_to_fid(path)
        if not result or isinstance(result, str) and not self._is_folder_fid(result):
            return None
        fid = result

        try:
            resp = client.get("/file/download", params={
                "fid": fid,
                "pr": "ucpro",
                "fr": "pc",
            })

            data = resp.get("data", {})
            download_url = data.get("download_url", "")
            if download_url:
                import urllib.request
                req = urllib.request.Request(download_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    content = response.read().decode("utf-8", errors="replace")
                    return content[:50000]  # 限制最大长度
            return None
        except Exception as e:
            print(f"下载文件失败: {e}")
            return None

    def _is_folder_fid(self, fid: str) -> bool:
        """检查 fid 是否对应文件夹"""
        client = self._get_api_client()
        try:
            resp = client.get("/file/sort", params={
                "pdir_fid": fid,
                "_page": "1",
                "_size": "1",
            })
            return resp.get("code") == 0
        except Exception:
            return False

    def add_to_knowledge_graph(self, title: str, content: str, domain: str = "engineering",
                                node_ids: Optional[List[str]] = None, notes: str = "") -> Optional[str]:
        """
        将内容添加为知识图谱节点

        Args:
            title: 知识标题
            content: 文件内容
            domain: 所属领域
            node_ids: 关联的课程节点 ID 列表
            notes: 笔记

        Returns:
            创建的节点 ID
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            # 生成唯一 ID
            node_id = f"quark_{int(time.time())}"

            # 提取摘要
            summary = self._extract_summary(content)

            # 插入节点
            cur.execute("""
                INSERT INTO nodes (id, title, domain, level, summary, why_matter)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                node_id, title, domain, "quark",
                summary,
                f"来源: 夸克网盘 - {title}"
            ))

            # 插入进度
            cur.execute("""
                INSERT INTO progress (node_id, status, learned_at, notes)
                VALUES (?, ?, ?, ?)
            """, (node_id, "pending", datetime.now().isoformat(), notes or f"从夸克网盘读取: {title}"))

            # 关联课程
            if node_ids:
                courses_id = f"quark_batch_{int(time.time())}"
                cur.execute("""
                    INSERT OR REPLACE INTO courses (id, title, node_ids, created_at, completed)
                    VALUES (?, ?, ?, datetime('now'), 0)
                """, (courses_id, title, json.dumps(node_ids, ensure_ascii=False)))

            conn.commit()
            print(f"✅ 已添加知识节点: {title} (ID: {node_id})")
            return node_id

        except Exception as e:
            conn.rollback()
            print(f"❌ 添加知识节点失败: {e}")
            return None
        finally:
            conn.close()

    def _extract_summary(self, content: str) -> str:
        """从内容中提取摘要"""
        # 取前 200 个字符作为摘要
        clean = content.replace("\n", " ").replace("\r", "").strip()
        return clean[:200] + ("..." if len(clean) > 200 else "")

    def read_and_store(self, path: str, title: Optional[str] = None,
                       domain: str = "engineering", notes: str = "") -> Optional[str]:
        """
        从夸克网盘读取文件并存储到知识图谱

        Args:
            path: 文件路径
            title: 知识标题，默认使用文件名
            domain: 所属领域
            notes: 笔记

        Returns:
            节点 ID
        """
        content = self.get_file_content(path)
        if not content:
            print(f"❌ 无法读取文件: {path}")
            return None

        if not title:
            title = path.split("/")[-1]

        return self.add_to_knowledge_graph(
            title=title,
            content=content,
            domain=domain,
            notes=notes
        )


def read_quark_file(path: str, title: Optional[str] = None,
                    domain: str = "engineering", notes: str = "") -> Optional[str]:
    """便捷函数：从夸克网盘读取文件并存储到知识图谱"""
    reader = QuarkReader()
    return reader.read_and_store(path, title, domain, notes)


def list_quark_folder(path: str) -> List[Dict[str, Any]]:
    """便捷函数：列出夸克网盘文件夹内容"""
    reader = QuarkReader()
    return reader.list_folder(path)
