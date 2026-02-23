
# Maintenance
Memory blocks that get too large create noise that buries other important information.
It's crucial that blocks stay dense. 

You can observe trends in block sizes by running:

```bash
uv run python ./.open_strix_builtin_skills/scripts/memory_dashboard.py
```

It uses matplotlib. It will 
generate a PNG image that can be attached into discord.

## Pruning Memory Blocks
If a block is too large, consider replacing chunks of block text with a file 
reference.

## File Frequency Report
The `./.open_strix_builtin_skills/scripts/file_frequency_report.py` script looks
through `logs/events.jsonl` to figure out which files are accessed most.

For heavily accessed files, consider moving important information into a memory 
block, to save on tool calls. If a heavily accessed file is also large, consider 
breaking it into smaller files.
