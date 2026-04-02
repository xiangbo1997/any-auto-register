from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, func
from pydantic import BaseModel
from core.db import AccountModel, get_session
from typing import Optional
from datetime import datetime, timezone
import io, csv, json, logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    platform: str
    email: str
    password: str
    status: str = "registered"
    token: str = ""
    cashier_url: str = ""
    access_token: str = ""
    refresh_token: str = ""


class AccountUpdate(BaseModel):
    status: Optional[str] = None
    token: Optional[str] = None
    cashier_url: Optional[str] = None
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None


class ImportRequest(BaseModel):
    platform: str
    lines: list[str]


class BatchDeleteRequest(BaseModel):
    ids: list[int]


@router.get("")
def list_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    email: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
):
    q = select(AccountModel)
    if platform:
        q = q.where(AccountModel.platform == platform)
    if status:
        q = q.where(AccountModel.status == status)
    if email:
        q = q.where(AccountModel.email.contains(email))
    total = len(session.exec(q).all())
    items = session.exec(q.offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "items": items}


@router.post("")
def create_account(body: AccountCreate, session: Session = Depends(get_session)):
    extra: dict = {}
    effective_token = body.token
    if body.access_token:
        extra["access_token"] = body.access_token
        if not effective_token:
            effective_token = body.access_token
    if body.refresh_token:
        extra["refresh_token"] = body.refresh_token
    acc = AccountModel(
        platform=body.platform,
        email=body.email,
        password=body.password,
        status=body.status,
        token=effective_token,
        cashier_url=body.cashier_url,
        extra_json=json.dumps(extra) if extra else "{}",
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):
    """统计各平台账号数量和状态分布"""
    accounts = session.exec(select(AccountModel)).all()
    platforms: dict = {}
    statuses: dict = {}
    for acc in accounts:
        platforms[acc.platform] = platforms.get(acc.platform, 0) + 1
        statuses[acc.status] = statuses.get(acc.status, 0) + 1
    return {"total": len(accounts), "by_platform": platforms, "by_status": statuses}


@router.get("/export")
def export_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    session: Session = Depends(get_session),
):
    q = select(AccountModel)
    if platform:
        q = q.where(AccountModel.platform == platform)
    if status:
        q = q.where(AccountModel.status == status)
    accounts = session.exec(q).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["platform", "email", "password", "user_id", "region",
                     "status", "cashier_url", "created_at"])
    for acc in accounts:
        writer.writerow([acc.platform, acc.email, acc.password, acc.user_id,
                         acc.region, acc.status, acc.cashier_url,
                         acc.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=accounts.csv"}
    )


@router.post("/import")
def import_accounts(
    body: ImportRequest,
    session: Session = Depends(get_session),
):
    """批量导入，支持两种格式:
    1. email----ms_password----client_id----refresh_token----chatgpt_password  (----分隔，5列，chatgpt_password 为第5列)
       也兼容4列: email----password----client_id----refresh_token
    2. email password [refresh_token 或 JSON extra]     (空格分隔)
    """
    created = 0
    for line in body.lines:
        raw = line.strip()
        if not raw:
            continue

        if "----" in raw:
            parts = raw.split("----")
            if len(parts) < 2:
                continue
            email = parts[0].strip()
            ms_password = parts[1].strip()           # Outlook 邮箱密码
            client_id = parts[2].strip() if len(parts) > 2 else ""
            refresh_token = parts[3].strip() if len(parts) > 3 else ""
            chatgpt_password = parts[4].strip() if len(parts) > 4 else ""

            # 第5列存在时作为 ChatGPT 登录密码，第2列归入 extra
            password = chatgpt_password if chatgpt_password else ms_password
            extra: dict = {}
            if client_id:
                extra["client_id"] = client_id
            if refresh_token:
                extra["refresh_token"] = refresh_token
            if chatgpt_password and ms_password:
                extra["ms_password"] = ms_password
        else:
            parts = raw.split()
            if len(parts) < 2:
                continue
            email, password = parts[0], parts[1]
            extra = {}
            if len(parts) > 2:
                third = parts[2]
                try:
                    parsed = json.loads(third)
                    if isinstance(parsed, dict):
                        extra = parsed
                except (json.JSONDecodeError, ValueError):
                    extra = {"refresh_token": third}

        acc = AccountModel(
            platform=body.platform,
            email=email,
            password=password,
            token="",
            extra_json=json.dumps(extra),
        )
        session.add(acc)
        created += 1
    session.commit()
    return {"created": created}


@router.post("/batch-delete")
def batch_delete_accounts(
    body: BatchDeleteRequest,
    session: Session = Depends(get_session)
):
    """批量删除账号"""
    if not body.ids:
        raise HTTPException(400, "账号 ID 列表不能为空")
    
    if len(body.ids) > 1000:
        raise HTTPException(400, "单次最多删除 1000 个账号")
    
    deleted_count = 0
    not_found_ids = []
    
    try:
        for account_id in body.ids:
            acc = session.get(AccountModel, account_id)
            if acc:
                session.delete(acc)
                deleted_count += 1
            else:
                not_found_ids.append(account_id)
        
        session.commit()
        logger.info(f"批量删除成功: {deleted_count} 个账号")
        
        return {
            "deleted": deleted_count,
            "not_found": not_found_ids,
            "total_requested": len(body.ids)
        }
    except Exception as e:
        session.rollback()
        logger.exception("批量删除失败")
        raise HTTPException(500, f"批量删除失败: {str(e)}")


@router.post("/check-all")
def check_all_accounts(platform: Optional[str] = None,
                       background_tasks: BackgroundTasks = None):
    from core.scheduler import scheduler
    background_tasks.add_task(scheduler.check_accounts_valid, platform)
    return {"message": "批量检测任务已启动"}


@router.get("/{account_id}")
def get_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return acc


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountUpdate,
                   session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if body.status is not None:
        acc.status = body.status
    if body.token is not None:
        acc.token = body.token
    if body.cashier_url is not None:
        acc.cashier_url = body.cashier_url
    if body.access_token is not None or body.refresh_token is not None:
        try:
            extra = json.loads(acc.extra_json or "{}")
        except (json.JSONDecodeError, ValueError):
            extra = {}
        if body.access_token is not None:
            extra["access_token"] = body.access_token
            if not acc.token or body.token is None:
                acc.token = body.access_token
        if body.refresh_token is not None:
            extra["refresh_token"] = body.refresh_token
        acc.extra_json = json.dumps(extra)
    acc.updated_at = datetime.now(timezone.utc)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.delete("/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    session.delete(acc)
    session.commit()
    return {"ok": True}


@router.post("/{account_id}/check")
def check_account(account_id: int, background_tasks: BackgroundTasks,
                  session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    background_tasks.add_task(_do_check, account_id)
    return {"message": "检测任务已启动"}


def _do_check(account_id: int):
    from core.db import engine
    from sqlmodel import Session
    with Session(engine) as s:
        acc = s.get(AccountModel, account_id)
    if acc:
        from core.base_platform import Account, RegisterConfig
        from core.registry import get
        try:
            PlatformCls = get(acc.platform)
            plugin = PlatformCls(config=RegisterConfig())
            obj = Account(platform=acc.platform, email=acc.email,
                         password=acc.password, user_id=acc.user_id,
                         region=acc.region, token=acc.token,
                         extra=json.loads(acc.extra_json or "{}"))
            valid = plugin.check_valid(obj)
            with Session(engine) as s:
                a = s.get(AccountModel, account_id)
                if a:
                    a.status = a.status if valid else "invalid"
                    a.updated_at = datetime.now(timezone.utc)
                    s.add(a)
                    s.commit()
        except Exception:
            logger.exception("检测账号 %s 时出错", account_id)
