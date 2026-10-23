"""Whitelisted web assets and backwards-compatible browser URLs."""
JS = 'text/javascript; charset=utf-8'
CSS = 'text/css; charset=utf-8'
STATIC_ASSETS = {
    '/assets/js/token-usage.js': ('assets/js/token-usage.js', JS),
    '/assets/js/game-workbench.js': ('assets/js/game-workbench.js', JS),
    '/assets/css/game-workbench.css': ('assets/css/game-workbench.css', CSS),
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/assets/js/app.js': ('assets/js/app.js', JS),
    '/assets/js/workbench.js': ('assets/js/workbench.js', JS),
    '/assets/js/gameplay/legacy-design.js': ('assets/js/gameplay/legacy-design.js', JS),
    '/assets/js/gameplay/recommendations.js': ('assets/js/gameplay/recommendations.js', JS),
    '/assets/css/base.css': ('assets/css/base.css', CSS),
    '/assets/css/gameplay.css': ('assets/css/gameplay.css', CSS),
    '/assets/css/validation.css': ('assets/css/validation.css', CSS),
    '/assets/css/workbench.css': ('assets/css/workbench.css', CSS),
    # Existing browser integrations can keep requesting these aliases.
    '/workbench.js': ('assets/js/workbench.js', JS),
    '/workbench.css': ('assets/css/workbench.css', CSS),
    '/gameplay-design.js': ('assets/js/gameplay/legacy-design.js', JS),
    '/gameplay-recommendations.js': ('assets/js/gameplay/recommendations.js', JS),
}
