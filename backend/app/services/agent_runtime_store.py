"""多 Agent 任务/工件存储；所有会话工件读写强制完整作用域。"""
import json, uuid
from sqlalchemy import text
from app.core.database import engine

def new_id(prefix: str) -> str: return f"{prefix}_{uuid.uuid4().hex[:16]}"

def create_task(session_id: str, user_id: int, space_id: str, question: str) -> dict:
    task_id = new_id("task")
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO analysis_tasks (id,session_id,user_id,space_id,question,status,current_agent) VALUES (:id,:sid,:uid,:space,:q,'queued','supervisor')"), {"id":task_id,"sid":session_id,"uid":user_id,"space":space_id,"q":question}); conn.commit()
    return {"id": task_id, "status": "queued", "current_agent": "supervisor"}

def save_artifact(task_id: str, session_id: str, user_id: int, space_id: str, artifact_type: str, source_agent: str, status: str, payload: dict) -> str:
    artifact_id = new_id("artifact")
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO agent_artifacts (id,task_id,session_id,user_id,space_id,artifact_type,source_agent,status,payload) VALUES (:id,:tid,:sid,:uid,:space,:type,:agent,:status,:payload)"), {"id":artifact_id,"tid":task_id,"sid":session_id,"uid":user_id,"space":space_id,"type":artifact_type,"agent":source_agent,"status":status,"payload":json.dumps(payload,ensure_ascii=False)}); conn.commit()
    return artifact_id

def list_artifacts(task_id: str, session_id: str, user_id: int, space_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows=conn.execute(text("SELECT id,artifact_type,source_agent,status,payload,created_at FROM agent_artifacts WHERE task_id=:tid AND session_id=:sid AND user_id=:uid AND space_id=:space ORDER BY created_at,id"), {"tid":task_id,"sid":session_id,"uid":user_id,"space":space_id}).fetchall()
    return [{"id":r[0],"type":r[1],"source_agent":r[2],"status":r[3],"payload":r[4] if isinstance(r[4],dict) else json.loads(r[4]),"created_at":str(r[5])} for r in rows]


# === Private working memory: (task_id, agent_name, session_id, user_id, space_id) ===

def get_working_memory(task_id: str, agent_name: str, session_id: str, user_id: int, space_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(text("""SELECT memory FROM agent_working_memories
            WHERE task_id=:tid AND agent_name=:agent AND session_id=:sid AND user_id=:uid AND space_id=:space"""), {"tid":task_id,"agent":agent_name,"sid":session_id,"uid":user_id,"space":space_id}).fetchone()
    if not row:
        return None
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]

def set_working_memory(task_id: str, agent_name: str, session_id: str, user_id: int, space_id: str, memory: dict) -> None:
    with engine.connect() as conn:
        conn.execute(text("""INSERT INTO agent_working_memories (task_id,agent_name,session_id,user_id,space_id,memory)
            VALUES (:tid,:agent,:sid,:uid,:space,:mem)
            ON DUPLICATE KEY UPDATE memory=VALUES(memory), updated_at=CURRENT_TIMESTAMP"""), {"tid":task_id,"agent":agent_name,"sid":session_id,"uid":user_id,"space":space_id,"mem":json.dumps(memory,ensure_ascii=False)})
        conn.commit()


# === Agent experience memory: (agent_name, space_id) only — space-isolated, cross-task ===

def get_experience_memory(agent_name: str, space_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT memory FROM agent_experience_memories WHERE agent_name=:agent AND space_id=:space"), {"agent":agent_name,"space":space_id}).fetchone()
    if not row:
        return None
    return json.loads(row[0]) if isinstance(row[0], str) else row[0]

def set_experience_memory(agent_name: str, space_id: str, memory: dict) -> None:
    with engine.connect() as conn:
        conn.execute(text("""INSERT INTO agent_experience_memories (agent_name,space_id,memory)
            VALUES (:agent,:space,:mem)
            ON DUPLICATE KEY UPDATE memory=VALUES(memory), updated_at=CURRENT_TIMESTAMP"""), {"agent":agent_name,"space":space_id,"mem":json.dumps(memory,ensure_ascii=False)})
        conn.commit()


def get_artifact(
    artifact_id: str,
    session_id: str,
    user_id: int,
    space_id: str,
) -> dict | None:
    """Fetch one artifact with full scope isolation."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, task_id, session_id, user_id, space_id, artifact_type,
                       source_agent, status, payload, created_at
                FROM agent_artifacts
                WHERE id=:id AND session_id=:sid AND user_id=:uid AND space_id=:space
                LIMIT 1
                """
            ),
            {"id": artifact_id, "sid": session_id, "uid": user_id, "space": space_id},
        ).fetchone()
    if not row:
        return None
    payload = row[8] if isinstance(row[8], dict) else json.loads(row[8] or "{}")
    return {
        "id": row[0],
        "task_id": row[1],
        "session_id": row[2],
        "user_id": row[3],
        "space_id": row[4],
        "type": row[5],
        "source_agent": row[6],
        "status": row[7],
        "payload": payload,
        "created_at": str(row[9]),
    }


def update_artifact_payload(
    artifact_id: str,
    session_id: str,
    user_id: int,
    space_id: str,
    payload: dict,
) -> bool:
    with engine.connect() as conn:
        res = conn.execute(
            text(
                """
                UPDATE agent_artifacts
                SET payload=:payload
                WHERE id=:id AND session_id=:sid AND user_id=:uid AND space_id=:space
                """
            ),
            {
                "id": artifact_id,
                "sid": session_id,
                "uid": user_id,
                "space": space_id,
                "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        conn.commit()
        return res.rowcount == 1


def list_latest_diagnosis_bundle(session_id: str, user_id: int, space_id: str) -> dict | None:
    """Return latest deep-diagnosis artifact bundle for a session (for reload UI).

    Scoped by session_id + user_id + space_id. No cross-session leak.
    """
    with engine.connect() as conn:
        # Find most recent ReportDocument (or ReviewResult) in this session scope
        row = conn.execute(
            text(
                """
                SELECT task_id FROM agent_artifacts
                WHERE session_id=:sid AND user_id=:uid AND space_id=:space
                  AND artifact_type IN ('ReportDocument', 'ReviewResult')
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"sid": session_id, "uid": user_id, "space": space_id},
        ).fetchone()
        if not row:
            return None
        task_id = row[0]
        rows = conn.execute(
            text(
                """
                SELECT id, artifact_type, source_agent, status, payload, created_at
                FROM agent_artifacts
                WHERE task_id=:tid AND session_id=:sid AND user_id=:uid AND space_id=:space
                ORDER BY created_at, id
                """
            ),
            {"tid": task_id, "sid": session_id, "uid": user_id, "space": space_id},
        ).fetchall()
    arts = []
    for r in rows:
        payload = r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}")
        arts.append(
            {
                "id": r[0],
                "type": r[1],
                "source_agent": r[2],
                "status": r[3],
                "payload": payload,
                "created_at": str(r[5]),
            }
        )
    report = next((a for a in arts if a["type"] == "ReportDocument"), None)
    review = next((a for a in reversed(arts) if a["type"] == "ReviewResult"), None)
    exports = [a for a in arts if a["type"] == "ExportFile"]
    if not report and not review:
        return None
    return {
        "task_id": task_id,
        "report": report,
        "review": review,
        "exports": exports,
        "artifacts": arts,
    }
