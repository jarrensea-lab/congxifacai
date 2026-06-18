"""持仓管理器单元测试 — PositionManager 所有公开方法"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.trading_engine.position import PositionManager


class TestPositionManager:
    def setup_method(self):
        """每个测试前建立内存 SQLite 数据库"""
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(bind=self.engine)
        self.db = Session(self.engine)

    def teardown_method(self):
        self.db.close()
        self.engine.dispose()

    # ===== get_or_create =====

    def test_get_or_create_new(self):
        pos = PositionManager.get_or_create(self.db, '000100', 'TCL科技')
        assert pos.stock_code == '000100'
        assert pos.stock_name == 'TCL科技'
        assert pos.quantity == 0
        assert pos.board_type == 'main'

    def test_get_or_create_existing(self):
        pos1 = PositionManager.get_or_create(self.db, '000100', 'TCL科技')
        pos2 = PositionManager.get_or_create(self.db, '000100', 'TCL科技')
        assert pos1.id == pos2.id
        assert pos2.quantity == 0  # unchanged

    # ===== update_on_buy =====

    def test_update_on_buy_new_position(self):
        pm = PositionManager()
        pm.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        pos = self.db.query(type(pm.get_one(self.db, '000100'))).first()
        pos = PositionManager.get_one(self.db, '000100')
        assert pos is not None
        assert pos.quantity == 100
        assert pos.avg_cost == 50000
        assert pos.market_value == 5000000
        assert pos.today_bought_qty == 100

    def test_update_on_buy_weighted_avg_cost(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 51000, 100, 5100000)
        pos = PositionManager.get_one(self.db, '000100')
        assert pos.quantity == 200
        assert pos.avg_cost == 50500  # (50000*100 + 51000*100) / 200
        assert pos.total_buy_amount == 10100000
        assert pos.total_buy_qty == 200

    def test_update_on_buy_different_stocks(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        PositionManager.update_on_buy(self.db, '600519', '贵州茅台', 200000, 10, 2000000)
        all_pos = PositionManager.get_all(self.db)
        assert len(all_pos) == 2

    # ===== update_on_sell =====

    def test_update_on_sell_partial(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 200, 10000000)
        PositionManager.update_on_sell(self.db, '000100', 'TCL科技', 52000, 50, 100000)
        pos = PositionManager.get_one(self.db, '000100')
        assert pos.quantity == 150

    def test_update_on_sell_full(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        PositionManager.update_on_sell(self.db, '000100', 'TCL科技', 52000, 100, 200000)
        pos = PositionManager.get_one(self.db, '000100')
        assert pos is None or pos.quantity == 0

    # ===== get =====

    def test_get_one_found(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        pos = PositionManager.get_one(self.db, '000100')
        assert pos is not None
        assert pos.stock_code == '000100'

    def test_get_one_not_found(self):
        pos = PositionManager.get_one(self.db, '999999')
        assert pos is None

    def test_get_all_empty(self):
        all_pos = PositionManager.get_all(self.db)
        assert all_pos == []

    def test_get_all_multiple(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        PositionManager.update_on_buy(self.db, '600519', '贵州茅台', 200000, 10, 2000000)
        all_pos = PositionManager.get_all(self.db)
        assert len(all_pos) == 2
        codes = {p.stock_code for p in all_pos}
        assert codes == {'000100', '600519'}

    # ===== display / value =====

    def test_get_holdings_display(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        display = PositionManager.get_holdings_display(self.db)
        assert len(display) == 1
        assert display[0]['stock_code'] == '000100'
        assert display[0]['stock_name'] == 'TCL科技'
        assert display[0]['quantity'] == 100
        assert display[0]['avg_cost'] == 500.0  # 50000 fen → 500 元

    def test_get_holdings_display_empty(self):
        display = PositionManager.get_holdings_display(self.db)
        assert display == []

    def test_get_total_market_value(self):
        PositionManager.update_on_buy(self.db, '000100', 'TCL科技', 50000, 100, 5000000)
        PositionManager.update_on_buy(self.db, '600519', '贵州茅台', 200000, 10, 2000000)
        total = PositionManager.get_total_market_value(self.db)
        assert total == 7000000  # 5000000 + 2000000

    def test_get_total_market_value_empty(self):
        total = PositionManager.get_total_market_value(self.db)
        assert total == 0
