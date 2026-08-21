import sqlite3
from config import DB_PATH


def write_audit(
    con=None,
    *,
    action: str,
    detail: str = "",
    worker_id: str = None,
    table_name: str = None,
    record_pk: str = None,
    operation_type: str = None,
    field_name: str = None,
    old_value: str = None,
    new_value: str = None,
):
    """
    監査ログを登録する。

    conを渡した場合は、呼び出し元のトランザクション内で記録する。
    conを渡さない場合は、この関数内で接続・確定する。
    """
    own_connection = con is None

    if own_connection:
        con = sqlite3.connect(DB_PATH)

    try:
        con.execute(
            """
            INSERT INTO audit_log (
                action,
                detail,
                worker_id,
                table_name,
                record_pk,
                operation_type,
                field_name,
                old_value,
                new_value
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                detail,
                worker_id,
                table_name,
                str(record_pk) if record_pk is not None else None,
                operation_type,
                field_name,
                str(old_value) if old_value is not None else None,
                str(new_value) if new_value is not None else None,
            ),
        )

        if own_connection:
            con.commit()

    except Exception:
        if own_connection:
            con.rollback()
        raise

    finally:
        if own_connection:
            con.close()


def get_audit_logs(
    table_name=None,
    record_pk=None,
    limit=500,
):
    """監査ログを取得する。"""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    try:
        conditions = []
        params = []

        if table_name:
            conditions.append("table_name = ?")
            params.append(table_name)

        if record_pk is not None:
            conditions.append("record_pk = ?")
            params.append(str(record_pk))

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        params.append(limit)

        rows = con.execute(
            f"""
            SELECT
                log_id,
                action,
                detail,
                created_at,
                worker_id,
                table_name,
                record_pk,
                operation_type,
                field_name,
                old_value,
                new_value
            FROM audit_log
            {where}
            ORDER BY log_id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        con.close()