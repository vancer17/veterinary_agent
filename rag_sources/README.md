# RAG source drop folder

Put legally available or licensed veterinary source documents here, for example exported free Merck Veterinary Manual pages or internally licensed notes.

Then import them into PostgreSQL:

```powershell
uv run python scripts/import_knowledge_dir.py --source-dir rag_sources --source "Merck Veterinary Manual free pages" --public-citation true
```

This repository intentionally does not scrape or vendor third-party manuals automatically. Each imported chunk keeps source metadata so output citation can follow the PRD copyright split.
