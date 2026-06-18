"""AKShare 新闻数据源 — 财经早餐 + 全球资讯 + 个股新闻"""
import asyncio
from typing import List, Optional


class AKShareNewsClient:
    """基于 AKShare 的统一新闻客户端

    数据来源:
    - stock_info_cjzc_em: 东方财富财经早餐 (宏观/政策/行业)
    - stock_info_global_em: 全球市场资讯
    - stock_news_em: 个股新闻 (含公告类)
    """

    def __init__(self):
        pass

    async def _call_async(self, fn, *args, **kwargs):
        """用 asyncio.to_thread 包装同步 AKShare 调用，60s 超时"""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs), timeout=60.0
            )
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    async def fetch_cjzc(self) -> Optional[list]:
        """获取东方财富财经早餐 (宏观/政策/行业要闻)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_info_cjzc_em)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(20).iterrows():
                title = str(row.get('标题', ''))
                summary = str(row.get('摘要', ''))[:200]
                time_str = str(row.get('发布时间', ''))
                text = title if title else summary
                if not text or text == 'nan':
                    continue
                results.append({'title': title, 'summary': summary, 'time': time_str})
            return results
        except Exception:
            return None

    async def fetch_global_news(self) -> Optional[list]:
        """获取全球市场资讯"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_info_global_em)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(15).iterrows():
                title = str(row.get('标题', ''))
                summary = str(row.get('摘要', ''))[:150]
                time_str = str(row.get('发布时间', ''))
                if not title or title == 'nan':
                    continue
                results.append({'title': title, 'summary': summary, 'time': time_str})
            return results
        except Exception:
            return None

    async def fetch_stock_news(self, code: str, limit: int = 5) -> Optional[list]:
        """获取个股新闻(含公告类)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_news_em, symbol=code)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(limit).iterrows():
                title = str(row.get('新闻标题', ''))
                content = str(row.get('新闻内容', ''))[:200]
                time_str = str(row.get('发布时间', ''))
                source = str(row.get('文章来源', ''))
                if not title or title == 'nan':
                    continue
                results.append({'title': title, 'content': content, 'time': time_str, 'source': source})
            return results
        except Exception:
            return None

    async def fetch_all_news(self, stock_codes: List[str] = None) -> str:
        """获取综合新闻摘要，格式化为 AI 可读文本"""
        parts = []

        # 1. 财经早餐 (宏观/政策/行业)
        cjzc = await self.fetch_cjzc()
        if cjzc:
            lines = ['【宏观/政策/行业要闻】(东方财富财经早餐)']
            for n in cjzc:
                title = n.get('title', '')
                summary = n.get('summary', '')
                text = title
                if summary and summary != 'nan' and summary not in title:
                    text += f' — {summary[:150]}'
                if text:
                    lines.append(f'· {text[:250]}')
            parts.append('\n'.join(lines))

        # 2. 全球市场资讯
        global_news = await self.fetch_global_news()
        if global_news:
            # 与财经早餐去重
            existing_titles = {n.get('title', '')[:30] for n in (cjzc or [])}
            new_global = [n for n in global_news if n.get('title', '')[:30] not in existing_titles]
            if new_global:
                lines = ['【全球市场参考】']
                for n in new_global[:10]:
                    title = n.get('title', '')
                    summary = n.get('summary', '')
                    text = title
                    if summary and summary != 'nan':
                        text += f' — {summary[:120]}'
                    if text:
                        lines.append(f'· {text[:250]}')
                parts.append('\n'.join(lines))

        # 3. 个股新闻
        if stock_codes:
            stock_lines = ['【个股相关动态】']
            for code in stock_codes[:5]:
                news = await self.fetch_stock_news(code, limit=3)
                if news:
                    for n in news:
                        title = n.get('title', '')
                        source = n.get('source', '')
                        content = n.get('content', '')
                        if title:
                            text = f'· [{code}] {title[:120]}'
                            if source and source != 'nan':
                                text += f' (来源: {source})'
                            if content and content != 'nan':
                                text += f' — {content[:150]}'
                            stock_lines.append(text)
            if len(stock_lines) > 1:
                parts.append('\n'.join(stock_lines))

        return '\n\n'.join(parts) if parts else ''
