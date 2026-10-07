"""RED code graph — page-to-page dependency edges derived from ``red_file_analyses``.

``derive``  pure functions: analysis rows -> nodes / edges (no database)
``dao``     reads ``red_file_analyses``, writes and traverses ``graph_nodes`` / ``graph_edges``
``build``   CLI: build the graph for a project from the table or from a RED JSON export
"""
