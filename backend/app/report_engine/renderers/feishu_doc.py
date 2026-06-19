"""飞书云文档上传 — 信息图PNG上传 + 云文档创建"""
import os
import subprocess
import json
import tempfile
from datetime import datetime
from typing import Optional
from app.config import settings
from app.utils.logger import logger


def upload_image_to_drive(image_path: str, file_name: Optional[str] = None) -> Optional[str]:
    """上传图片到飞书云盘，返回 file_token"""
    if not file_name:
        file_name = os.path.basename(image_path)
    lark_cli = getattr(settings, "LARK_CLI_PATH", "/Users/zhuchenyuan/.npm-global/bin/lark-cli")
    try:
        result = subprocess.run(
            [lark_cli, "drive", "+upload-media", "--file-path", image_path, "--file-name", file_name],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip()
        if output:
            try:
                data = json.loads(output.split("\n")[-1])
                return data.get("data", {}).get("file_token")
            except (json.JSONDecodeError, KeyError):
                pass
        logger.warning(f"飞书上传失败: {result.stderr[:200]}")
        return None
    except Exception as e:
        logger.error(f"飞书上传异常: {e}")
        return None


def create_doc_from_markdown(title: str, markdown_content: str) -> Optional[str]:
    """创建飞书云文档并写入Markdown内容，返回文档链接"""
    lark_cli = getattr(settings, "LARK_CLI_PATH", "/Users/zhuchenyuan/.npm-global/bin/lark-cli")
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(markdown_content)
            tmp_path = f.name
        result = subprocess.run(
            [lark_cli, "docx", "+create", "--title", title, "--content-file", tmp_path],
            capture_output=True, text=True, timeout=30
        )
        os.unlink(tmp_path)
        output = result.stdout.strip()
        if output:
            try:
                data = json.loads(output.split("\n")[-1])
                return data.get("data", {}).get("url") or data.get("url")
            except (json.JSONDecodeError, KeyError):
                pass
        logger.warning(f"创建飞书文档失败: {result.stderr[:200]}")
        return None
    except Exception as e:
        logger.error(f"创建飞书文档异常: {e}")
        return None


def create_doc_with_image(title: str, image_paths: list[str]) -> Optional[str]:
    """创建飞书云文档并插入多张图片，返回文档链接"""
    try:
        file_tokens = []
        for img_path in image_paths:
            token = upload_image_to_drive(img_path)
            if token:
                file_tokens.append(token)

        doc_content = f"# {title}\n\n"
        doc_content += f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        for i, token in enumerate(file_tokens):
            doc_content += f"![图表{i+1}](https://internal-api-drive-stream.feishu.cn/space/api/box/stream/download/all/{token})\n\n"

        return create_doc_from_markdown(title, doc_content)
    except Exception as e:
        logger.error(f"创建图片文档异常: {e}")
        return None
