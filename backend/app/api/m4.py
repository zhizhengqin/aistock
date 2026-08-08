from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.response import success
from app.models.user import User
from app.models.task_record import TaskRecord
from app.models.portfolio_stock import PortfolioStock
from app.models.portfolio_report import PortfolioReport
from app.models.monitor_config import MonitorConfig
from app.models.monitor_notification import MonitorNotification
from app.models.risk_warning import RiskWarning
from app.core.logger import logger
from pydantic import BaseModel
import asyncio

router = APIRouter()


def _start_task(db, task_type, user_id, inline_func, args):
    task = TaskRecord(task_type=task_type, user_id=user_id, status="pending", progress=0)
    db.add(task); db.commit(); db.refresh(task)
    if settings.TASK_INLINE:
        asyncio.create_task(inline_func(None, task.id, *args))
        logger.info(f"Inline task {task.id} type={task_type}")
    return task


# ---------------------------------------------------------------------------
# Portfolio stocks CRUD (F-07-01)
# ---------------------------------------------------------------------------

class PortfolioStockCreate(BaseModel):
    stock_code: str
    stock_name: str = ""
    shares: int
    cost_price: float
    auto_monitor: bool = False


class PortfolioStockUpdate(BaseModel):
    shares: int | None = None
    cost_price: float | None = None
    auto_monitor: bool | None = None


@router.get("/stocks/portfolio/stocks")
async def list_portfolio_stocks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == user.id).all()
    return success(data=[{
        "id": s.id, "stock_code": s.stock_code, "stock_name": s.stock_name,
        "shares": s.shares, "cost_price": s.cost_price, "auto_monitor": s.auto_monitor,
        "current_price": s.current_price, "market_value": s.market_value,
        "profit_loss": s.profit_loss, "profit_pct": s.profit_pct, "industry": s.industry,
    } for s in stocks])


@router.post("/stocks/portfolio/stocks")
async def add_portfolio_stock(req: PortfolioStockCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    existing = db.query(PortfolioStock).filter(
        PortfolioStock.user_id == user.id, PortfolioStock.stock_code == req.stock_code
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="该股票已在持仓中")
    stock = PortfolioStock(
        user_id=user.id, stock_code=req.stock_code, stock_name=req.stock_name,
        shares=req.shares, cost_price=req.cost_price, auto_monitor=req.auto_monitor,
    )
    db.add(stock); db.commit(); db.refresh(stock)
    return success(data={"id": stock.id}, message="添加成功")


@router.put("/stocks/portfolio/stocks/{stock_id}")
async def update_portfolio_stock(stock_id: int, req: PortfolioStockUpdate,
                                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stock = db.query(PortfolioStock).filter(PortfolioStock.id == stock_id, PortfolioStock.user_id == user.id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="持仓不存在")
    if req.shares is not None: stock.shares = req.shares
    if req.cost_price is not None: stock.cost_price = req.cost_price
    if req.auto_monitor is not None: stock.auto_monitor = req.auto_monitor
    db.commit()
    return success(message="更新成功")


@router.delete("/stocks/portfolio/stocks/{stock_id}")
async def delete_portfolio_stock(stock_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stock = db.query(PortfolioStock).filter(PortfolioStock.id == stock_id, PortfolioStock.user_id == user.id).first()
    if not stock:
        raise HTTPException(status_code=404, detail="持仓不存在")
    db.delete(stock); db.commit()
    return success(message="删除成功")


@router.get("/stocks/portfolio/summary")
async def portfolio_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == user.id).all()
    total_cost = sum(s.shares * s.cost_price for s in stocks)
    total_market = sum(s.market_value for s in stocks)
    total_pl = total_market - total_cost
    total_pl_pct = round((total_pl / total_cost) * 100, 2) if total_cost > 0 else 0
    monitoring_count = sum(1 for s in stocks if s.auto_monitor)
    return success(data={
        "total_stocks": len(stocks), "total_cost": round(total_cost, 2),
        "total_market_value": round(total_market, 2),
        "total_profit_loss": round(total_pl, 2), "total_profit_pct": total_pl_pct,
        "monitoring_count": monitoring_count,
    })


# ---------------------------------------------------------------------------
# Portfolio AI diagnosis (F-07-03)
# ---------------------------------------------------------------------------

@router.post("/stocks/portfolio/analyze")
async def analyze_portfolio(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == user.id).count()
    if stocks == 0:
        raise HTTPException(status_code=400, detail="请先添加持仓股票")
    task = _start_task(db, "portfolio_diagnosis", user.id,
                       __import__("app.tasks.portfolio", fromlist=["portfolio_diagnosis_task"]).portfolio_diagnosis_task,
                       [user.id])
    return success(data={"task_id": task.id}, message="持仓诊断任务已提交")


@router.get("/stocks/portfolio/history")
async def portfolio_history(page: int = 1, page_size: int = 20,
                              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total = db.query(func.count(PortfolioReport.id)).filter(PortfolioReport.user_id == user.id).scalar() or 0
    reports = db.query(PortfolioReport).filter(PortfolioReport.user_id == user.id) \
        .order_by(PortfolioReport.created_at.desc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    return success(data={"total": total, "page": page, "page_size": page_size, "items": [
        {"id": r.id, "health_score": r.health_score,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in reports
    ]})


@router.get("/stocks/portfolio/reports/{report_id}")
async def portfolio_report_detail(report_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    report = db.query(PortfolioReport).filter(PortfolioReport.id == report_id, PortfolioReport.user_id == user.id).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return success(data=report.diagnosis_json)


# ---------------------------------------------------------------------------
# Monitor configs CRUD (F-08-02)
# ---------------------------------------------------------------------------

class MonitorConfigCreate(BaseModel):
    stock_code: str
    stock_name: str = ""
    entry_price: float = 0
    target_price: float = 0
    stop_price: float = 0
    profit_pct: float = 10
    loss_pct: float = 5
    interval_min: int = 10
    channels: str = "in_app"
    ai_enabled: bool = False


@router.get("/stocks/ai-monitoring/configurations")
async def list_monitor_configs(status: str = "all", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(MonitorConfig).filter(MonitorConfig.user_id == user.id)
    if status != "all":
        q = q.filter(MonitorConfig.status == status)
    configs = q.order_by(MonitorConfig.created_at.desc()).all()
    return success(data=[{
        "id": c.id, "stock_code": c.stock_code, "stock_name": c.stock_name,
        "entry_price": c.entry_price, "target_price": c.target_price, "stop_price": c.stop_price,
        "profit_pct": c.profit_pct, "loss_pct": c.loss_pct, "interval_min": c.interval_min,
        "channels": c.channels, "ai_enabled": c.ai_enabled, "status": c.status,
        "last_checked_at": c.last_checked_at,
    } for c in configs])


@router.post("/stocks/ai-monitoring/configurations")
async def add_monitor_config(req: MonitorConfigCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = MonitorConfig(user_id=user.id, **req.model_dump())
    db.add(config); db.commit(); db.refresh(config)
    return success(data={"id": config.id}, message="监测项添加成功")


@router.patch("/stocks/ai-monitoring/configurations/{config_id}")
async def update_monitor_config(config_id: int, req: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(MonitorConfig).filter(MonitorConfig.id == config_id, MonitorConfig.user_id == user.id).first()
    if not config:
        raise HTTPException(status_code=404, detail="监测项不存在")
    for k, v in req.items():
        if hasattr(config, k): setattr(config, k, v)
    db.commit()
    return success(message="更新成功")


@router.delete("/stocks/ai-monitoring/configurations/{config_id}")
async def delete_monitor_config(config_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    config = db.query(MonitorConfig).filter(MonitorConfig.id == config_id, MonitorConfig.user_id == user.id).first()
    if not config:
        raise HTTPException(status_code=404, detail="监测项不存在")
    db.delete(config); db.commit()
    return success(message="删除成功")


# ---------------------------------------------------------------------------
# Monitor notifications (F-08-03)
# ---------------------------------------------------------------------------

@router.get("/stocks/ai-monitoring/notifications")
async def list_notifications(status: str = "pending", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    notifications = db.query(MonitorNotification).filter(
        MonitorNotification.user_id == user.id,
        MonitorNotification.status == status,
    ).order_by(MonitorNotification.created_at.desc()).all()
    return success(data=[{
        "id": n.id, "config_id": n.config_id, "stock_code": n.stock_code,
        "stock_name": n.stock_name, "ntype": n.ntype, "title": n.title,
        "content": n.content, "status": n.status,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    } for n in notifications])


@router.patch("/stocks/ai-monitoring/notifications/{notif_id}")
async def update_notification(notif_id: int, req: dict, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.query(MonitorNotification).filter(MonitorNotification.id == notif_id, MonitorNotification.user_id == user.id).first()
    if not n:
        raise HTTPException(status_code=404, detail="通知不存在")
    if "status" in req: n.status = req["status"]
    db.commit()
    return success(message="更新成功")


@router.post("/stocks/ai-monitoring/check")
async def trigger_monitor_check(user: User = Depends(get_current_user)):
    from app.services.monitor_engine import run_monitor_check
    count = run_monitor_check()
    return success(data={"triggered": count}, message="检查完成")


# ---------------------------------------------------------------------------
# Risk analysis (F-09)
# ---------------------------------------------------------------------------

class RiskAnalyzeRequest(BaseModel):
    stock_code: str
    days: int = 30


@router.post("/stocks/risk/analyze")
async def analyze_stock_risk(req: RiskAnalyzeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.days < 1 or req.days > 365:
        raise HTTPException(status_code=400, detail="分析天数范围1-365")
    task = _start_task(db, "stock_risk", user.id,
                       __import__("app.tasks.risk_analysis", fromlist=["stock_risk_task"]).stock_risk_task,
                       [req.stock_code, req.days, user.id])
    return success(data={"task_id": task.id}, message="风险分析任务已提交")


@router.get("/stocks/risk/portfolio")
async def portfolio_risk(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Return existing risk warnings for user's portfolio
    warnings = db.query(RiskWarning).filter(RiskWarning.user_id == user.id) \
        .order_by(RiskWarning.created_at.desc()).limit(100).all()
    if not warnings:
        # No existing warnings — trigger a scan
        stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == user.id).count()
        if stocks == 0:
            return success(data={"total_warnings": 0, "max_level": "info",
                                  "composite_score": 100, "level_stats": {},
                                  "warnings_detail": []}, message="暂无持仓，无法分析")
        task = _start_task(db, "portfolio_risk", user.id,
                           __import__("app.tasks.portfolio_risk", fromlist=["portfolio_risk_task"]).portfolio_risk_task,
                           [user.id])
        return success(data={"task_id": task.id}, message="组合风险扫描已提交")

    level_counts = {}
    for w in warnings:
        level_counts[w.level] = level_counts.get(w.level, 0) + 1

    return success(data={
        "total_warnings": len(warnings),
        "max_level": max(level_counts.keys(), key=lambda l: {"info": 1, "warning": 2, "danger": 3, "critical": 4}.get(l, 0)) if level_counts else "info",
        "level_stats": level_counts,
        "warnings_detail": [{
            "id": w.id, "level": w.level, "category": w.category,
            "stock_code": w.stock_code, "stock_name": w.stock_name,
            "message": w.message, "value": w.value,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        } for w in warnings]
    })


@router.get("/stocks/risk/active")
async def active_risk_warnings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="全市场活跃预警为管理员专属功能")
    warnings = db.query(RiskWarning).filter(RiskWarning.user_id == None) \
        .order_by(RiskWarning.created_at.desc()).limit(100).all()
    return success(data=[{
        "id": w.id, "level": w.level, "category": w.category,
        "stock_code": w.stock_code, "stock_name": w.stock_name,
        "message": w.message, "value": w.value,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    } for w in warnings])
None
