# C4 Container

Диаграмма фиксирует контейнеры и крупные технологические блоки внутри single-node PoC.

```mermaid
flowchart TB
    user["Продакт-менеджер"]

    subgraph pfia["PFIA PoC / Docker Compose"]
        api["FastAPI Web App<br/>API + Static UI"]
        worker["Worker / Orchestrator"]
        tool["Tool Layer"]

        subgraph storage["Storage"]
            sql["SQLite (WAL)<br/>jobs, sessions, checkpoints"]
            index["On-disk retrieval index<br/>session-scoped pickle artifacts"]
            files["Local volume<br/>uploads, sanitized artifacts, reports"]
        end

        subgraph observability["Observability"]
            metrics["/metrics"]
            logs["JSON logs"]
            traces["OTel traces"]
        end
    end

    openai["OpenAI API"]
    langsmith["LangSmith / OTLP sink"]

    user --> api
    api --> sql
    api --> files
    api --> worker
    worker --> sql
    worker --> files
    worker --> index
    worker --> tool
    tool --> index
    tool --> sql
    worker --> openai
    api --> metrics
    worker --> metrics
    api --> logs
    worker --> logs
    api --> traces
    worker --> traces
    traces --> langsmith
```
