"""AKShare 市场数据源 — 资金流向 + 行业板块 + 龙虎榜 + 沪深港通"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional


class AKShareMarketClient:
    """基于 AKShare 的市场数据客户端

    数据来源 (均为同花顺 data.10jqka.com.cn — 非 push2.eastmoney.com):
    - stock_fund_flow_individual: 个股资金流向 (主力净流入/流出)
    - stock_fund_flow_industry: 行业资金流向排名
    - stock_fund_flow_concept: 概念资金流向
    - stock_fund_flow_big_deal: 大单追踪
    - stock_board_industry_name_ths: 同花顺行业分类
    - stock_board_industry_index_ths: 行业指数K线
    - stock_hsgt_fund_flow_summary_em: 沪深港通资金
    - stock_lhb_ggtj_sina: 龙虎榜统计
    - stock_zh_index_spot_sina: 主要指数行情
    """

    async def _call_async(self, fn, *args, **kwargs):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs), timeout=60.0
            )
        except (asyncio.TimeoutError, Exception):
            return None

    # ═══ 资金流向 ═══

    async def fetch_fund_flow_individual(self) -> Optional[list]:
        """获取全市场个股资金流向 (同花顺)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_fund_flow_individual, '即时')
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.iterrows():
                code = str(row.get('股票代码', ''))
                name = str(row.get('股票简称', ''))
                inflow = str(row.get('流入资金', ''))
                outflow = str(row.get('流出资金', ''))
                net = str(row.get('净额', ''))
                change_pct = str(row.get('涨跌幅', ''))
                turnover = str(row.get('换手率', ''))
                if not code or code == 'nan':
                    continue
                results.append({
                    'code': code, 'name': name, 'inflow': inflow,
                    'outflow': outflow, 'net': net, 'change_pct': change_pct,
                    'turnover': turnover,
                })
            return results
        except Exception:
            return None

    async def fetch_fund_flow_industry(self) -> Optional[list]:
        """获取行业资金流向排名 (同花顺)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_fund_flow_industry, '即时')
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(30).iterrows():
                name = str(row.get('行业', ''))
                net = str(row.get('净额', ''))
                inflow = str(row.get('流入资金', ''))
                outflow = str(row.get('流出资金', ''))
                change_pct = str(row.get('行业-涨跌幅', ''))
                leader = str(row.get('领涨股', ''))
                if not name or name == 'nan':
                    continue
                results.append({
                    'name': name, 'net': net, 'inflow': inflow,
                    'outflow': outflow, 'change_pct': change_pct, 'leader': leader,
                })
            return results
        except Exception:
            return None

    async def fetch_fund_flow_concept(self) -> Optional[list]:
        """获取概念资金流向 (同花顺)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_fund_flow_concept, '即时')
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(20).iterrows():
                name = str(row.get('行业', ''))
                net = str(row.get('净额', ''))
                change_pct = str(row.get('行业-涨跌幅', ''))
                leader = str(row.get('领涨股', ''))
                if not name or name == 'nan':
                    continue
                results.append({
                    'name': name, 'net': net, 'change_pct': change_pct, 'leader': leader,
                })
            return results
        except Exception:
            return None

    async def fetch_big_deals(self) -> Optional[list]:
        """获取大单追踪 (同花顺)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_fund_flow_big_deal)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(30).iterrows():
                name = str(row.get('股票简称', ''))
                price = str(row.get('成交价格', ''))
                volume = str(row.get('成交量', ''))
                amount = str(row.get('成交额', ''))
                direction = str(row.get('大单性质', ''))
                change_pct = str(row.get('涨跌幅', ''))
                if not name or name == 'nan':
                    continue
                results.append({
                    'name': name, 'price': price, 'volume': volume,
                    'amount': amount, 'direction': direction, 'change_pct': change_pct,
                })
            return results
        except Exception:
            return None

    # ═══ 行业板块 ═══

    async def fetch_ths_industries(self) -> Optional[list]:
        """获取同花顺行业分类列表"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_board_industry_name_ths)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.iterrows():
                results.append({'name': str(row['name']), 'code': str(row['code'])})
            return results
        except Exception:
            return None

    async def fetch_industry_index(self, name: str = '白酒') -> Optional[list]:
        """获取行业指数近期K线 (同花顺)"""
        try:
            import akshare as ak
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
            df = await self._call_async(ak.stock_board_industry_index_ths, name,
                                        start_date, end_date)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.iterrows():
                results.append({
                    'date': str(row.get('日期', ''))[:10],
                    'open': str(row.get('开盘价', '')),
                    'close': str(row.get('收盘价', '')),
                    'high': str(row.get('最高价', '')),
                    'low': str(row.get('最低价', '')),
                    'volume': str(row.get('成交量', '')),
                })
            return results
        except Exception:
            return None

    # ═══ 市场全景 ═══

    async def fetch_hsgt_flow(self) -> Optional[list]:
        """获取沪深港通资金流向"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_hsgt_fund_flow_summary_em)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.iterrows():
                results.append({
                    'date': str(row.get('交易日', '')),
                    'type': str(row.get('类型', '')),
                    'board': str(row.get('板块', '')),
                    'direction': str(row.get('资金方向', '')),
                    'net': str(row.get('成交净买额', '')),
                    'balance': str(row.get('当日资金余额', '')),
                })
            return results
        except Exception:
            return None

    async def fetch_lhb_stats(self) -> Optional[list]:
        """获取龙虎榜统计 (新浪)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_lhb_ggtj_sina)
            if df is None or df.empty:
                return None
            results = []
            for _, row in df.head(20).iterrows():
                results.append({
                    'code': str(row.get('股票代码', '')),
                    'name': str(row.get('股票名称', '')),
                    'count': str(row.get('上榜次数', '')),
                    'buy': str(row.get('累积购买额', '')),
                    'sell': str(row.get('累积卖出额', '')),
                    'net': str(row.get('净额', '')),
                })
            return results
        except Exception:
            return None

    async def fetch_market_indices(self) -> Optional[list]:
        """获取主要指数行情 (新浪)"""
        try:
            import akshare as ak
            df = await self._call_async(ak.stock_zh_index_spot_sina)
            if df is None or df.empty:
                return None
            key_indices = ['上证指数', '深证成指', '创业板指', '科创50', '沪深300']
            results = []
            for _, row in df.iterrows():
                name = str(row.get('名称', ''))
                if name in key_indices:
                    results.append({
                        'name': name,
                        'price': str(row.get('最新价', '')),
                        'change_pct': str(row.get('涨跌幅', '')),
                        'change_amt': str(row.get('涨跌额', '')),
                        'volume': str(row.get('成交量', '')),
                        'amount': str(row.get('成交额', '')),
                    })
            return results
        except Exception:
            return None

    # ═══ 综合汇总 ═══

    async def fetch_all_market_data(self, stock_codes: List[str] = None) -> str:
        """获取综合市场数据，格式化为 AI 可读文本"""
        parts = []

        # 1. 主要指数
        indices = await self.fetch_market_indices()
        if indices:
            lines = ['【主要指数】']
            for idx in indices:
                lines.append(
                    f"· {idx['name']}: {idx['price']} "
                    f"({idx['change_pct']}% {idx['change_amt']}) "
                    f"成交{idx['amount']}元"
                )
            parts.append('\n'.join(lines))

        # 2. 行业资金流向 Top15
        industry_flow = await self.fetch_fund_flow_industry()
        if industry_flow:
            lines = ['【行业资金流向 Top15】']
            for ind in industry_flow[:15]:
                lines.append(
                    f"· {ind['name']}: 净额{ind['net']} "
                    f"涨跌{ind['change_pct']}% 领涨:{ind['leader']}"
                )
            parts.append('\n'.join(lines))

        # 3. 概念资金流向 Top10
        concept_flow = await self.fetch_fund_flow_concept()
        if concept_flow:
            lines = ['【热门概念资金 Top10】']
            for c in concept_flow[:10]:
                lines.append(
                    f"· {c['name']}: 净额{c['net']} 涨跌{c['change_pct']}% 领涨:{c['leader']}"
                )
            parts.append('\n'.join(lines))

        # 4. 个股资金流向 (自选股)
        if stock_codes:
            all_flow = await self.fetch_fund_flow_individual()
            if all_flow:
                flow_map = {f['code']: f for f in all_flow}
                lines = ['【自选股资金流向】']
                for code in stock_codes[:10]:
                    pure_code = code.replace('sh', '').replace('sz', '').replace('bj', '')
                    if pure_code in flow_map:
                        f = flow_map[pure_code]
                        lines.append(
                            f"· [{f['code']}] {f['name']}: "
                            f"净额{f['net']} 流入{f['inflow']} 流出{f['outflow']} "
                            f"涨跌{f['change_pct']}% 换手{f['turnover']}%"
                        )
                    else:
                        lines.append(f'· [{code}] 暂无资金流向数据')
                parts.append('\n'.join(lines))

        # 5. 沪深港通
        hsgt = await self.fetch_hsgt_flow()
        if hsgt:
            lines = ['【沪深港通资金】']
            for h in hsgt:
                lines.append(
                    f"· {h['type']} {h['board']} {h['direction']}: "
                    f"净买额{h['net']} 余额{h['balance']}"
                )
            parts.append('\n'.join(lines))

        # 6. 龙虎榜
        lhb = await self.fetch_lhb_stats()
        if lhb:
            lines = ['【龙虎榜统计】']
            for l in lhb[:10]:
                lines.append(
                    f"· [{l['code']}] {l['name']}: 上榜{l['count']}次 "
                    f"买入{l['buy']} 卖出{l['sell']} 净额{l['net']}"
                )
            parts.append('\n'.join(lines))

        # 7. 大单追踪
        big_deals = await self.fetch_big_deals()
        if big_deals:
            lines = ['【大单追踪 (近期)】']
            for d in big_deals[:10]:
                lines.append(
                    f"· {d['name']}: {d['direction']} {d['volume']}手 "
                    f"¥{d['price']} 金额{d['amount']}万 涨跌{d['change_pct']}"
                )
            parts.append('\n'.join(lines))

        return '\n\n'.join(parts) if parts else ''
