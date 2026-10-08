"""Internationalisation (i18n) de l'interface MIrAI.

Catalogues Python purs (aucune dependance externe, pas de gettext compile) pour
les chaines visibles par l'utilisateur dans les boites de dialogue de
configuration. Le francais est la langue source ; l'extension suit la langue
de LibreOffice, et l'anglais quand elle ne la propose pas.

Ce module vit sous `src/mirai/` et NON sous `core/` ou `ui/` : la regle
d'architecture `tests/unit/core/test_no_entrypoint_import.py` interdit la
sous-chaine "entrypoint" dans ces deux paquets, et la facade `core` doit rester
independante de la coquille UNO.

Usage:
    from .i18n import t, set_locale, resolve_locale
    set_locale(resolve_locale(ctx))
    label = t("proxy.section")
"""

DEFAULT_LOCALE = "fr"

# Langue retenue quand celle de LibreOffice n'est pas proposee par l'extension.
FALLBACK_LOCALE = "en"

SUPPORTED = ("fr", "en", "es", "pt", "zh")


CATALOG = {
    # --- Boite de dialogue Parametres proxy ---------------------------------
    "proxy.title": {
        "fr": "Proxy",
        "en": "Proxy",
        "es": "Proxy",
        "pt": "Proxy",
        "zh": "代理",
    },
    "proxy.section": {
        "fr": "Paramètres proxy",
        "en": "Proxy settings",
        "es": "Configuración del proxy",
        "pt": "Configurações de proxy",
        "zh": "代理设置",
    },
    "proxy.enabled": {
        "fr": "Utiliser un proxy :",
        "en": "Use a proxy:",
        "es": "Usar un proxy:",
        "pt": "Usar um proxy:",
        "zh": "使用代理：",
    },
    "proxy.url": {
        "fr": "Proxy (host:port) :",
        "en": "Proxy (host:port):",
        "es": "Proxy (host:puerto):",
        "pt": "Proxy (host:porta):",
        "zh": "代理（主机:端口）：",
    },
    "proxy.username": {
        "fr": "Login proxy (optionnel) :",
        "en": "Proxy login (optional):",
        "es": "Usuario del proxy (opcional):",
        "pt": "Login do proxy (opcional):",
        "zh": "代理登录名（可选）：",
    },
    "proxy.password": {
        "fr": "Mot de passe proxy (optionnel) :",
        "en": "Proxy password (optional):",
        "es": "Contraseña del proxy (opcional):",
        "pt": "Senha do proxy (opcional):",
        "zh": "代理密码（可选）：",
    },
    "proxy.insecure": {
        "fr": "Autoriser HTTPS sans vérification (-k) :",
        "en": "Allow HTTPS without verification (-k):",
        "es": "Permitir HTTPS sin verificación (-k):",
        "pt": "Permitir HTTPS sem verificação (-k):",
        "zh": "允许不验证 HTTPS（-k）：",
    },
    "proxy.lo_prefix": {
        "fr": "Proxy LibreOffice : ",
        "en": "LibreOffice proxy: ",
        "es": "Proxy de LibreOffice: ",
        "pt": "Proxy do LibreOffice: ",
        "zh": "LibreOffice 代理：",
    },
    "proxy.lo_disabled": {
        "fr": "désactivé",
        "en": "disabled",
        "es": "desactivado",
        "pt": "desativado",
        "zh": "已禁用",
    },
    "proxy.test_button": {
        "fr": "Tester connexion",
        "en": "Test connection",
        "es": "Probar conexión",
        "pt": "Testar conexão",
        "zh": "测试连接",
    },
    "proxy.copy_lo": {
        "fr": "Copier depuis LibreOffice",
        "en": "Copy from LibreOffice",
        "es": "Copiar desde LibreOffice",
        "pt": "Copiar do LibreOffice",
        "zh": "从 LibreOffice 复制",
    },
    "proxy.test_title": {
        "fr": "Test proxy",
        "en": "Proxy test",
        "es": "Prueba del proxy",
        "pt": "Teste do proxy",
        "zh": "代理测试",
    },
    "proxy.test_failed": {
        "fr": "Échec: {detail}",
        "en": "Failed: {detail}",
        "es": "Error: {detail}",
        "pt": "Falha: {detail}",
        "zh": "失败：{detail}",
    },
    # --- Boutons et libelles communs ----------------------------------------
    "common.save": {
        "fr": "Enregistrer",
        "en": "Save",
        "es": "Guardar",
        "pt": "Salvar",
        "zh": "保存",
    },
    "common.cancel": {
        "fr": "Annuler",
        "en": "Cancel",
        "es": "Cancelar",
        "pt": "Cancelar",
        "zh": "取消",
    },
    "app.title": {
        "fr": "MIrAI — Paramètres",
        "en": "MIrAI — Settings",
        "es": "MIrAI — Configuración",
        "pt": "MIrAI — Configurações",
        "zh": "MIrAI — 设置",
    },
    # --- Boite de dialogue Parametres ---------------------------------------
    "settings.connecting": {
        "fr": "Contacte MIrAI...",
        "en": "Contacting MIrAI...",
        "es": "Contactando con MIrAI...",
        "pt": "Contatando o MIrAI...",
        "zh": "正在连接 MIrAI...",
    },
    "settings.connecting_base": {
        "fr": "Contacte MIrAI",
        "en": "Contacting MIrAI",
        "es": "Contactando con MIrAI",
        "pt": "Contatando o MIrAI",
        "zh": "正在连接 MIrAI",
    },
    "settings.endpoint_label": {
        "fr": "OWUI Endpoint:",
        "en": "OWUI endpoint:",
        "es": "Endpoint OWUI:",
        "pt": "Endpoint OWUI:",
        "zh": "OWUI 端点：",
    },
    "settings.api_key_label": {
        "fr": "Token OWUI:",
        "en": "OWUI token:",
        "es": "Token OWUI:",
        "pt": "Token OWUI:",
        "zh": "OWUI 令牌：",
    },
    "settings.model_label": {
        "fr": "Model:",
        "en": "Model:",
        "es": "Modelo:",
        "pt": "Modelo:",
        "zh": "模型：",
    },
    "settings.model_desc_label": {
        "fr": "Description du modèle :",
        "en": "Model description:",
        "es": "Descripción del modelo:",
        "pt": "Descrição do modelo:",
        "zh": "模型说明：",
    },
    "settings.show": {
        "fr": "Révéler",
        "en": "Reveal",
        "es": "Mostrar",
        "pt": "Revelar",
        "zh": "显示",
    },
    "settings.hide": {
        "fr": "Masquer",
        "en": "Hide",
        "es": "Ocultar",
        "pt": "Ocultar",
        "zh": "隐藏",
    },
    "settings.refresh_token": {
        "fr": "♻️ Rafraîchir le token",
        "en": "♻️ Refresh token",
        "es": "♻️ Actualizar el token",
        "pt": "♻️ Atualizar o token",
        "zh": "♻️ 刷新令牌",
    },
    "settings.section_connection": {
        "fr": "Connexion",
        "en": "Connection",
        "es": "Conexión",
        "pt": "Conexão",
        "zh": "连接",
    },
    "settings.proxy_button": {
        "fr": "Proxy",
        "en": "Proxy",
        "es": "Proxy",
        "pt": "Proxy",
        "zh": "代理",
    },
    "settings.section_status": {
        "fr": "État de la connexion",
        "en": "Connection status",
        "es": "Estado de la conexión",
        "pt": "Estado da conexão",
        "zh": "连接状态",
    },
    "settings.status_connected": {
        "fr": "Connecté",
        "en": "Connected",
        "es": "Conectado",
        "pt": "Conectado",
        "zh": "已连接",
    },
    "settings.status_anonymous": {
        "fr": "Anonyme OK",
        "en": "Anonymous OK",
        "es": "Anónimo OK",
        "pt": "Anônimo OK",
        "zh": "匿名可用",
    },
    "settings.status_untested": {
        "fr": "Non testé",
        "en": "Not tested",
        "es": "Sin probar",
        "pt": "Não testado",
        "zh": "未测试",
    },
    "settings.status_unreachable": {
        "fr": "Non accessible",
        "en": "Unreachable",
        "es": "No accesible",
        "pt": "Inacessível",
        "zh": "无法访问",
    },
    "settings.sso_login": {
        "fr": "🔐 Login SSO",
        "en": "🔐 SSO login",
        "es": "🔐 Inicio de sesión SSO",
        "pt": "🔐 Login SSO",
        "zh": "🔐 单点登录",
    },
    "settings.reload_config": {
        "fr": "🔄 Recharger la configuration",
        "en": "🔄 Reload configuration",
        "es": "🔄 Recargar la configuración",
        "pt": "🔄 Recarregar a configuração",
        "zh": "🔄 重新加载配置",
    },
    "settings.reload_title": {
        "fr": "Connexion à Mirai...",
        "en": "Connecting to Mirai...",
        "es": "Conectando con Mirai...",
        "pt": "Conectando ao Mirai...",
        "zh": "正在连接 Mirai...",
    },
    "settings.reload_title_base": {
        "fr": "Connexion à Mirai",
        "en": "Connecting to Mirai",
        "es": "Conectando con Mirai",
        "pt": "Conectando ao Mirai",
        "zh": "正在连接 Mirai",
    },
    "settings.reload_failed": {
        "fr": "Impossible de recharger la configuration.",
        "en": "Unable to reload the configuration.",
        "es": "No se puede recargar la configuración.",
        "pt": "Não foi possível recarregar a configuração.",
        "zh": "无法重新加载配置。",
    },
    "settings.reload_dialog_title": {
        "fr": "Configuration",
        "en": "Configuration",
        "es": "Configuración",
        "pt": "Configuração",
        "zh": "配置",
    },
    "settings.models_failed_title": {
        "fr": "API",
        "en": "API",
        "es": "API",
        "pt": "API",
        "zh": "API",
    },
    "settings.models_failed": {
        "fr": "Erreur lors de la récupération des modèles (vérifiez l'endpoint et le token).",
        "en": "Error while fetching the models (check the endpoint and the token).",
        "es": "Error al obtener los modelos (compruebe el endpoint y el token).",
        "pt": "Erro ao obter os modelos (verifique o endpoint e o token).",
        "zh": "获取模型时出错（请检查端点和令牌）。",
    },
    "settings.no_description": {
        "fr": "Aucune description disponible",
        "en": "No description available",
        "es": "No hay descripción disponible",
        "pt": "Nenhuma descrição disponível",
        "zh": "暂无说明",
    },
    "settings.id_prefix": {
        "fr": "ID: {value}",
        "en": "ID: {value}",
        "es": "ID: {value}",
        "pt": "ID: {value}",
        "zh": "ID：{value}",
    },
    "settings.token_error_unknown": {
        "fr": "inconnue",
        "en": "unknown",
        "es": "desconocido",
        "pt": "desconhecido",
        "zh": "未知",
    },
    "settings.token_error_proxy": {
        "fr": "Endpoint OWUI injoignable via le proxy.\n\nURL testée: {url}\nDétail: {detail}\n\nVérifiez le proxy (bouton Proxy > Tester connexion).",
        "en": "OWUI endpoint unreachable through the proxy.\n\nTested URL: {url}\nDetail: {detail}\n\nCheck the proxy (Proxy button > Test connection).",
        "es": "No se puede acceder al endpoint OWUI a través del proxy.\n\nURL probada: {url}\nDetalle: {detail}\n\nCompruebe el proxy (botón Proxy > Probar conexión).",
        "pt": "Endpoint OWUI inacessível através do proxy.\n\nURL testada: {url}\nDetalhe: {detail}\n\nVerifique o proxy (botão Proxy > Testar conexão).",
        "zh": "无法通过代理访问 OWUI 端点。\n\n测试的 URL：{url}\n详情：{detail}\n\n请检查代理（代理按钮 > 测试连接）。",
    },
    "settings.token_error_unreachable": {
        "fr": "Endpoint OWUI injoignable.\n\nURL testée: {url}\nDétail: {detail}",
        "en": "OWUI endpoint unreachable.\n\nTested URL: {url}\nDetail: {detail}",
        "es": "No se puede acceder al endpoint OWUI.\n\nURL probada: {url}\nDetalle: {detail}",
        "pt": "Endpoint OWUI inacessível.\n\nURL testada: {url}\nDetalhe: {detail}",
        "zh": "无法访问 OWUI 端点。\n\n测试的 URL：{url}\n详情：{detail}",
    },
    "settings.token_invalid": {
        "fr": "Token invalide, absent, ou refusé.",
        "en": "Invalid, missing, or rejected token.",
        "es": "Token no válido, ausente o rechazado.",
        "pt": "Token inválido, ausente ou recusado.",
        "zh": "令牌无效、缺失或被拒绝。",
    },
    "settings.no_models": {
        "fr": "Aucun modèle disponible (vérifiez l'endpoint et le token).",
        "en": "No model available (check the endpoint and the token).",
        "es": "No hay ningún modelo disponible (compruebe el endpoint y el token).",
        "pt": "Nenhum modelo disponível (verifique o endpoint e o token).",
        "zh": "没有可用的模型（请检查端点和令牌）。",
    },
    # --- Barre de menu / barre d'outils (oxt/Addons.xcu) ---------------------
    "addon.menubar": {
        "fr": "🤖 MIrAI",
        "en": "🤖 MIrAI",
        "es": "🤖 MIrAI",
        "pt": "🤖 MIrAI",
        "zh": "🤖 MIrAI",
    },
    "addon.open_assistant": {
        "fr": "🤖 Ouvrir l'assistant (Ctrl+Alt+Espace)",
        "en": "🤖 Open the assistant (Ctrl+Alt+Space)",
        "es": "🤖 Abrir el asistente (Ctrl+Alt+Espacio)",
        "pt": "🤖 Abrir o assistente (Ctrl+Alt+Espaço)",
        "zh": "🤖 打开助手（Ctrl+Alt+空格）",
    },
    "addon.settings": {
        "fr": "⚙️ Paramètres",
        "en": "⚙️ Settings",
        "es": "⚙️ Configuración",
        "pt": "⚙️ Configurações",
        "zh": "⚙️ 设置",
    },
    "addon.test_model": {
        "fr": "🔬 Tester le modèle",
        "en": "🔬 Test the model",
        "es": "🔬 Probar el modelo",
        "pt": "🔬 Testar o modelo",
        "zh": "🔬 测试模型",
    },
    "addon.documentation": {
        "fr": "📚 Documentation",
        "en": "📚 Documentation",
        "es": "📚 Documentación",
        "pt": "📚 Documentação",
        "zh": "📚 文档",
    },
    "addon.about": {
        "fr": "ℹ️ À propos de l'IA'ssistant...",
        "en": "ℹ️ About the IA'ssistant...",
        "es": "ℹ️ Acerca del IA'ssistant...",
        "pt": "ℹ️ Sobre o IA'ssistant...",
        "zh": "ℹ️ 关于 IA'ssistant...",
    },
    "addon.toolbar": {
        "fr": "Assistant MIrAI",
        "en": "MIrAI assistant",
        "es": "Asistente MIrAI",
        "pt": "Assistente MIrAI",
        "zh": "MIrAI 助手",
    },

    # --- Libelles communs supplementaires ---
    "common.ok": {
        "fr": "OK",
        "en": "OK",
        "es": "OK",
        "pt": "OK",
        "zh": "确定",
    },
    "common.close": {
        "fr": "Fermer",
        "en": "Close",
        "es": "Cerrar",
        "pt": "Fechar",
        "zh": "关闭",
    },
    "common.send": {
        "fr": "Envoyer",
        "en": "Send",
        "es": "Enviar",
        "pt": "Enviar",
        "zh": "发送",
    },
    "common.confirm": {
        "fr": "Confirmer",
        "en": "Confirm",
        "es": "Confirmar",
        "pt": "Confirmar",
        "zh": "确认",
    },
    "common.error": {
        "fr": "Erreur : {detail}",
        "en": "Error: {detail}",
        "es": "Error: {detail}",
        "pt": "Erro: {detail}",
        "zh": "错误：{detail}",
    },
    "common.suggestions": {
        "fr": "Suggestions...",
        "en": "Suggestions...",
        "es": "Sugerencias...",
        "pt": "Sugestões...",
        "zh": "建议…",
    },
    "common.new_suggestions": {
        "fr": "  Nouvelles suggestions",
        "en": "  New suggestions",
        "es": "  Nuevas sugerencias",
        "pt": "  Novas sugestões",
        "zh": "  新建议",
    },

    # --- Boite de dialogue A propos ---
    "about.title": {
        "fr": "À propos de l'IA'ssistant MIrAI",
        "en": "About IA'ssistant MIrAI",
        "es": "Acerca de IA'ssistant MIrAI",
        "pt": "Sobre o IA'ssistant MIrAI",
        "zh": "关于 IA'ssistant MIrAI",
    },
    "about.window_title": {
        "fr": "MIrAI — IA'ssistant LibreOffice",
        "en": "MIrAI — IA'ssistant LibreOffice",
        "es": "MIrAI — IA'ssistant LibreOffice",
        "pt": "MIrAI — IA'ssistant LibreOffice",
        "zh": "MIrAI — LibreOffice 智能助手",
    },
    "about.version": {
        "fr": "Version {version}",
        "en": "Version {version}",
        "es": "Versión {version}",
        "pt": "Versão {version}",
        "zh": "版本 {version}",
    },
    "about.desc": {
        "fr": (
            "Extension LibreOffice intégrant un assistant IA dans Writer et Calc. "
            "Sélectionnez du texte et utilisez le menu MIrAI pour générer, modifier, "
            "résumer, reformuler ou ajuster la longueur de vos documents."
        ),
        "en": (
            "LibreOffice extension bringing an AI assistant into Writer and Calc. "
            "Select text and use the MIrAI menu to generate, edit, summarize, "
            "rephrase or adjust the length of your documents."
        ),
        "es": (
            "Extensión de LibreOffice que integra un asistente de IA en Writer y Calc. "
            "Seleccione texto y use el menú MIrAI para generar, editar, resumir, "
            "reformular o ajustar la longitud de sus documentos."
        ),
        "pt": (
            "Extensão do LibreOffice que integra um assistente de IA no Writer e no Calc. "
            "Selecione texto e use o menu MIrAI para gerar, editar, resumir, "
            "reformular ou ajustar o comprimento dos seus documentos."
        ),
        "zh": (
            "在 Writer 和 Calc 中集成 AI 助手的 LibreOffice 扩展。"
            "选中文本并使用 MIrAI 菜单来生成、编辑、摘要、改写或调整文档长度。"
        ),
    },
    "about.program": {
        "fr": "Programme MIrAI — Ministère de l'Intérieur",
        "en": "MIrAI Programme — Ministry of the Interior",
        "es": "Programa MIrAI — Ministerio del Interior",
        "pt": "Programa MIrAI — Ministério do Interior",
        "zh": "MIrAI 计划 — 内政部",
    },
    "about.changelog_title": {
        "fr": "Derniers ajouts",
        "en": "Latest additions",
        "es": "Últimas novedades",
        "pt": "Últimas novidades",
        "zh": "最新功能",
    },
    "about.changelog": {
        "fr": (
            "• Ajuster la longueur — mini-dialogue − / + pour réduire ou développer\n"
            "• Suggestions IA contextuelles dans le dialogue d'édition\n"
            "• Analyse de plage Calc avec nettoyage markdown\n"
            "• Déploiement automatisé avec rollout progressif\n"
            "• Notice utilisateur double persona (novice / expert)\n"
            "• Filtrage robuste du raisonnement LLM (blocs <think>)"
        ),
        "en": (
            "• Adjust length — − / + mini-dialog to shorten or expand\n"
            "• Contextual AI suggestions in the edit dialog\n"
            "• Calc range analysis with markdown cleanup\n"
            "• Automated deployment with progressive rollout\n"
            "• Dual-persona user guide (beginner / expert)\n"
            "• Robust filtering of LLM reasoning (<think> blocks)"
        ),
        "es": (
            "• Ajustar la longitud — mini-diálogo − / + para reducir o ampliar\n"
            "• Sugerencias de IA contextuales en el diálogo de edición\n"
            "• Análisis de rangos de Calc con limpieza de markdown\n"
            "• Despliegue automatizado con lanzamiento progresivo\n"
            "• Guía de usuario con doble perfil (novato / experto)\n"
            "• Filtrado robusto del razonamiento del LLM (bloques <think>)"
        ),
        "pt": (
            "• Ajustar o comprimento — minidiálogo − / + para reduzir ou ampliar\n"
            "• Sugestões de IA contextuais no diálogo de edição\n"
            "• Análise de intervalo do Calc com limpeza de markdown\n"
            "• Implantação automatizada com lançamento progressivo\n"
            "• Guia do usuário com dois perfis (iniciante / especialista)\n"
            "• Filtragem robusta do raciocínio do LLM (blocos <think>)"
        ),
        "zh": (
            "• 调整长度 — 使用 − / + 小对话框缩短或扩展\n"
            "• 编辑对话框中的上下文 AI 建议\n"
            "• Calc 区域分析并清理 markdown\n"
            "• 带渐进式发布的自动化部署\n"
            "• 双角色用户指南（新手 / 专家）\n"
            "• 稳健过滤 LLM 推理（<think> 块）"
        ),
    },
    "about.updates_button": {
        "fr": "  Mises à jour",
        "en": "  Updates",
        "es": "  Actualizaciones",
        "pt": "  Atualizações",
        "zh": "  更新",
    },
    "about.open_folder": {
        "fr": "Ouvrir dossier",
        "en": "Open folder",
        "es": "Abrir carpeta",
        "pt": "Abrir pasta",
        "zh": "打开文件夹",
    },
    "about.checking": {
        "fr": "Vérification en cours...",
        "en": "Checking...",
        "es": "Comprobando...",
        "pt": "Verificando...",
        "zh": "正在检查…",
    },
    "about.selftest_blocked": {
        "fr": "Self-test : boîte « mise à jour bloquée »",
        "en": "Self-test: \"update blocked\" box",
        "es": "Autoprueba: cuadro «actualización bloqueada»",
        "pt": "Autoteste: caixa «atualização bloqueada»",
        "zh": "自检：“更新被阻止”对话框",
    },
    "about.update_available": {
        "fr": "Version {target} disponible. Mise à jour lancée...",
        "en": "Version {target} available. Update started...",
        "es": "Versión {target} disponible. Actualización iniciada...",
        "pt": "Versão {target} disponível. Atualização iniciada...",
        "zh": "版本 {target} 可用。更新已启动…",
    },
    "about.downloading": {
        "fr": "Téléchargement de la v{target} en cours...",
        "en": "Downloading v{target}...",
        "es": "Descargando la v{target}...",
        "pt": "Baixando a v{target}...",
        "zh": "正在下载 v{target}…",
    },
    "about.installed_restart": {
        "fr": "v{target} installée. Redémarrez LibreOffice.",
        "en": "v{target} installed. Restart LibreOffice.",
        "es": "v{target} instalada. Reinicie LibreOffice.",
        "pt": "v{target} instalada. Reinicie o LibreOffice.",
        "zh": "v{target} 已安装。请重启 LibreOffice。",
    },
    "about.download_failed": {
        "fr": "Échec du téléchargement de la v{target}.",
        "en": "Failed to download v{target}.",
        "es": "Error al descargar la v{target}.",
        "pt": "Falha ao baixar a v{target}.",
        "zh": "下载 v{target} 失败。",
    },
    "about.uptodate": {
        "fr": "Version {current} — à jour.",
        "en": "Version {current} — up to date.",
        "es": "Versión {current} — actualizada.",
        "pt": "Versão {current} — atualizada.",
        "zh": "版本 {current} — 已是最新。",
    },
    "about.folder_opened": {
        "fr": "Dossier ouvert.",
        "en": "Folder opened.",
        "es": "Carpeta abierta.",
        "pt": "Pasta aberta.",
        "zh": "文件夹已打开。",
    },
    "about.folder_failed": {
        "fr": "Impossible d'ouvrir le dossier.",
        "en": "Unable to open the folder.",
        "es": "No se pudo abrir la carpeta.",
        "pt": "Não foi possível abrir a pasta.",
        "zh": "无法打开文件夹。",
    },

    # --- Dialogues de mise a jour ---
    "update.title": {
        "fr": "MIrAI — Mise à jour",
        "en": "MIrAI — Update",
        "es": "MIrAI — Actualización",
        "pt": "MIrAI — Atualização",
        "zh": "MIrAI — 更新",
    },
    "update.blocked_title": {
        "fr": "MIrAI — Mise à jour bloquée",
        "en": "MIrAI — Update blocked",
        "es": "MIrAI — Actualización bloqueada",
        "pt": "MIrAI — Atualização bloqueada",
        "zh": "MIrAI — 更新被阻止",
    },
    "update.prompt": {
        "fr": (
            "MIrAI {version} est prêt.\n\n"
            "Pour l'installer, LibreOffice va se fermer —\n"
            "vous le rouvrirez ensuite pour l'activer.\n\n"
            "Installer et fermer maintenant ?\n\n"
            "(Si vous choisissez Non, la mise à jour sera\n"
            "reproposée plus tard. Vous pouvez aussi la\n"
            "lancer depuis le menu MIrAI → À propos…)"
        ),
        "en": (
            "MIrAI {version} is ready.\n\n"
            "To install it, LibreOffice will close —\n"
            "you will then reopen it to activate the update.\n\n"
            "Install and close now?\n\n"
            "(If you choose No, the update will be\n"
            "offered again later. You can also start it\n"
            "from the MIrAI → About… menu.)"
        ),
        "es": (
            "MIrAI {version} está listo.\n\n"
            "Para instalarlo, LibreOffice se cerrará;\n"
            "vuelva a abrirlo después para activarlo.\n\n"
            "¿Instalar y cerrar ahora?\n\n"
            "(Si elige No, la actualización se volverá\n"
            "a proponer más tarde. También puede iniciarla\n"
            "desde el menú MIrAI → Acerca de…)"
        ),
        "pt": (
            "O MIrAI {version} está pronto.\n\n"
            "Para instalá-lo, o LibreOffice será fechado;\n"
            "reabra-o em seguida para ativá-lo.\n\n"
            "Instalar e fechar agora?\n\n"
            "(Se você escolher Não, a atualização será\n"
            "oferecida novamente mais tarde. Você também pode\n"
            "iniciá-la pelo menu MIrAI → Sobre…)"
        ),
        "zh": (
            "MIrAI {version} 已准备就绪。\n\n"
            "安装时 LibreOffice 将关闭，\n"
            "之后请重新打开以启用新版本。\n\n"
            "现在安装并关闭吗？\n\n"
            "（如果选择“否”，稍后会再次提示更新。\n"
            "您也可以从菜单 MIrAI → 关于… 启动更新。）"
        ),
    },
    "update.prompt_critical": {
        "fr": (
            "Une nouvelle version de MIrAI ({version})\n"
            "avec des améliorations importantes est prête.\n\n"
            "Pour l'installer, LibreOffice va se fermer —\n"
            "rouvrez-le ensuite pour l'activer.\n\n"
            "Installer et fermer maintenant ?\n\n"
            "(Si vous choisissez Non, la mise à jour sera\n"
            "reproposée plus tard.)"
        ),
        "en": (
            "A new version of MIrAI ({version})\n"
            "with important improvements is ready.\n\n"
            "To install it, LibreOffice will close —\n"
            "reopen it afterwards to activate the update.\n\n"
            "Install and close now?\n\n"
            "(If you choose No, the update will be\n"
            "offered again later.)"
        ),
        "es": (
            "Una nueva versión de MIrAI ({version})\n"
            "con mejoras importantes está lista.\n\n"
            "Para instalarla, LibreOffice se cerrará;\n"
            "vuelva a abrirlo después para activarla.\n\n"
            "¿Instalar y cerrar ahora?\n\n"
            "(Si elige No, la actualización se volverá\n"
            "a proponer más tarde.)"
        ),
        "pt": (
            "Uma nova versão do MIrAI ({version})\n"
            "com melhorias importantes está pronta.\n\n"
            "Para instalá-la, o LibreOffice será fechado;\n"
            "reabra-o em seguida para ativá-la.\n\n"
            "Instalar e fechar agora?\n\n"
            "(Se você escolher Não, a atualização será\n"
            "oferecida novamente mais tarde.)"
        ),
        "zh": (
            "MIrAI 的新版本（{version}）已准备就绪，\n"
            "其中包含重要改进。\n\n"
            "安装时 LibreOffice 将关闭，\n"
            "之后请重新打开以启用新版本。\n\n"
            "现在安装并关闭吗？\n\n"
            "（如果选择“否”，稍后会再次提示更新。）"
        ),
    },
    "update.activates_at_restart": {
        "fr": "La mise à jour s'activera au prochain démarrage de LibreOffice.",
        "en": "The update will be activated the next time LibreOffice starts.",
        "es": "La actualización se activará en el próximo inicio de LibreOffice.",
        "pt": "A atualização será ativada na próxima inicialização do LibreOffice.",
        "zh": "更新将在下次启动 LibreOffice 时生效。",
    },
    "update.installed_closing": {
        "fr": (
            "La mise à jour a été installée.\n\n"
            "LibreOffice va se fermer pour l'activer.\n"
            "Rouvrez-le ensuite."
        ),
        "en": (
            "The update has been installed.\n\n"
            "LibreOffice will close to activate it.\n"
            "Reopen it afterwards."
        ),
        "es": (
            "La actualización se ha instalado.\n\n"
            "LibreOffice se cerrará para activarla.\n"
            "Vuelva a abrirlo después."
        ),
        "pt": (
            "A atualização foi instalada.\n\n"
            "O LibreOffice será fechado para ativá-la.\n"
            "Reabra-o em seguida."
        ),
        "zh": (
            "更新已安装。\n\n"
            "LibreOffice 将关闭以启用更新。\n"
            "之后请重新打开。"
        ),
    },
    "update.blocked_body": {
        "fr": (
            "La mise à jour MIrAI {version} a été téléchargée et\n"
            "vérifiée, mais son installation automatique a été bloquée par\n"
            "la politique de sécurité de ce poste.\n\n"
            "Elle ne sera plus reproposée automatiquement — vous pouvez\n"
            "l'installer vous-même :\n\n"
            "── Installation manuelle ─────────────────────────\n"
            "1. Menu  Outils ▸ Gestionnaire des extensions…\n"
            "2. Si « MIrAI » est déjà dans la liste : sélectionnez-le,\n"
            "   puis cliquez sur « Supprimer ».\n"
            "3. Cliquez sur « Ajouter » et sélectionnez le fichier :\n"
            "      {oxt}\n"
            "4. Acceptez la licence.\n"
            "5. Fermez puis rouvrez LibreOffice.\n\n"
            "(La suppression/ajout se fait dans LibreOffice — pas besoin\n"
            "de droits administrateur.)\n"
            "En cas d'échec, contactez votre support / administrateur."
        ),
        "en": (
            "The MIrAI {version} update was downloaded and\n"
            "verified, but its automatic installation was blocked by\n"
            "the security policy of this computer.\n\n"
            "It will not be offered again automatically — you can\n"
            "install it yourself:\n\n"
            "── Manual installation ───────────────────────────\n"
            "1. Menu  Tools ▸ Extension Manager…\n"
            "2. If \"MIrAI\" is already in the list: select it,\n"
            "   then click \"Remove\".\n"
            "3. Click \"Add\" and select the file:\n"
            "      {oxt}\n"
            "4. Accept the license.\n"
            "5. Close and reopen LibreOffice.\n\n"
            "(Removing and adding happen inside LibreOffice — no\n"
            "administrator rights needed.)\n"
            "If it fails, contact your support / administrator."
        ),
        "es": (
            "La actualización MIrAI {version} se ha descargado y\n"
            "verificado, pero su instalación automática ha sido bloqueada por\n"
            "la política de seguridad de este equipo.\n\n"
            "No se volverá a proponer automáticamente; puede\n"
            "instalarla usted mismo:\n\n"
            "── Instalación manual ────────────────────────────\n"
            "1. Menú  Herramientas ▸ Gestor de extensiones…\n"
            "2. Si «MIrAI» ya está en la lista: selecciónelo\n"
            "   y haga clic en «Quitar».\n"
            "3. Haga clic en «Añadir» y seleccione el archivo:\n"
            "      {oxt}\n"
            "4. Acepte la licencia.\n"
            "5. Cierre y vuelva a abrir LibreOffice.\n\n"
            "(Quitar y añadir se hace dentro de LibreOffice; no se necesitan\n"
            "derechos de administrador.)\n"
            "Si falla, contacte con su soporte / administrador."
        ),
        "pt": (
            "A atualização MIrAI {version} foi baixada e\n"
            "verificada, mas a instalação automática foi bloqueada pela\n"
            "política de segurança deste computador.\n\n"
            "Ela não será mais oferecida automaticamente; você pode\n"
            "instalá-la por conta própria:\n\n"
            "── Instalação manual ─────────────────────────────\n"
            "1. Menu  Ferramentas ▸ Gerenciador de extensões…\n"
            "2. Se «MIrAI» já estiver na lista: selecione-o\n"
            "   e clique em «Remover».\n"
            "3. Clique em «Adicionar» e selecione o arquivo:\n"
            "      {oxt}\n"
            "4. Aceite a licença.\n"
            "5. Feche e reabra o LibreOffice.\n\n"
            "(Remover e adicionar é feito dentro do LibreOffice; não são\n"
            "necessários direitos de administrador.)\n"
            "Em caso de falha, contate seu suporte / administrador."
        ),
        "zh": (
            "MIrAI {version} 更新已下载并通过校验，\n"
            "但本机的安全策略阻止了自动安装。\n\n"
            "系统不会再自动提示此更新，您可以自行安装：\n\n"
            "── 手动安装 ─────────────────────────────────\n"
            "1. 菜单  工具 ▸ 扩展管理器…\n"
            "2. 如果列表中已有“MIrAI”：选中它，\n"
            "   然后点击“移除”。\n"
            "3. 点击“添加”并选择文件：\n"
            "      {oxt}\n"
            "4. 接受许可协议。\n"
            "5. 关闭并重新打开 LibreOffice。\n\n"
            "（移除和添加均在 LibreOffice 内完成，无需管理员权限。）\n"
            "如果失败，请联系您的支持人员或管理员。"
        ),
    },
    "update.blocked_pending_folder": {
        "fr": "le dossier pending_update de votre profil LibreOffice",
        "en": "the pending_update folder of your LibreOffice profile",
        "es": "la carpeta pending_update de su perfil de LibreOffice",
        "pt": "a pasta pending_update do seu perfil do LibreOffice",
        "zh": "您的 LibreOffice 配置文件中的 pending_update 文件夹",
    },
    "update.blocked_open_folder": {
        "fr": (
            "► Pour ouvrir le dossier contenant le fichier téléchargé,\n"
            "  cliquez sur « Oui » : l'explorateur de fichiers s'ouvre\n"
            "  directement (sans invite de commande). « Non » referme\n"
            "  simplement ce message."
        ),
        "en": (
            "► To open the folder containing the downloaded file,\n"
            "  click \"Yes\": the file explorer opens\n"
            "  directly (no command prompt). \"No\" simply\n"
            "  closes this message."
        ),
        "es": (
            "► Para abrir la carpeta que contiene el archivo descargado,\n"
            "  haga clic en «Sí»: el explorador de archivos se abre\n"
            "  directamente (sin símbolo del sistema). «No» simplemente\n"
            "  cierra este mensaje."
        ),
        "pt": (
            "► Para abrir a pasta que contém o arquivo baixado,\n"
            "  clique em «Sim»: o explorador de arquivos abre\n"
            "  diretamente (sem prompt de comando). «Não» apenas\n"
            "  fecha esta mensagem."
        ),
        "zh": (
            "► 要打开包含已下载文件的文件夹，\n"
            "  请点击“是”：文件资源管理器会直接打开\n"
            "  （不经过命令提示符）。“否”仅关闭此消息。"
        ),
    },

    # --- Boite de dialogue Ajuster la longueur ---
    "resize.title": {
        "fr": "MIrAI — Ajuster la longueur",
        "en": "MIrAI — Adjust length",
        "es": "MIrAI — Ajustar la longitud",
        "pt": "MIrAI — Ajustar o comprimento",
        "zh": "MIrAI — 调整长度",
    },
    "resize.hint": {
        "fr": "Sélectionnez du texte puis cliquez − ou +",
        "en": "Select text then click − or +",
        "es": "Seleccione texto y haga clic en − o +",
        "pt": "Selecione texto e clique em − ou +",
        "zh": "选中文本，然后点击 − 或 +",
    },
    "resize.no_selection": {
        "fr": "Sélectionnez du texte à ajuster.",
        "en": "Select the text to adjust.",
        "es": "Seleccione el texto que desea ajustar.",
        "pt": "Selecione o texto a ajustar.",
        "zh": "请选中要调整的文本。",
    },
    "resize.reduce_running": {
        "fr": "Mirai réduit...",
        "en": "Mirai is shortening...",
        "es": "Mirai está reduciendo...",
        "pt": "Mirai está reduzindo...",
        "zh": "Mirai 正在缩短…",
    },
    "resize.expand_running": {
        "fr": "Mirai développe...",
        "en": "Mirai is expanding...",
        "es": "Mirai está ampliando...",
        "pt": "Mirai está ampliando...",
        "zh": "Mirai 正在扩展…",
    },
    "resize.no_result": {
        "fr": "Aucun résultat. Réessayez.",
        "en": "No result. Try again.",
        "es": "Sin resultados. Inténtelo de nuevo.",
        "pt": "Nenhum resultado. Tente novamente.",
        "zh": "没有结果。请重试。",
    },
    "resize.undo_reduce": {
        "fr": "Réduire",
        "en": "Shorten",
        "es": "Reducir",
        "pt": "Reduzir",
        "zh": "缩短",
    },
    "resize.undo_expand": {
        "fr": "Développer",
        "en": "Expand",
        "es": "Ampliar",
        "pt": "Ampliar",
        "zh": "扩展",
    },
    "resize.ok_format": {
        "fr": "OK ({new_word_count} mots, {sign}{delta}). Ctrl+Z pour annuler.",
        "en": "OK ({new_word_count} words, {sign}{delta}). Ctrl+Z to undo.",
        "es": "OK ({new_word_count} palabras, {sign}{delta}). Ctrl+Z para deshacer.",
        "pt": "OK ({new_word_count} palavras, {sign}{delta}). Ctrl+Z para desfazer.",
        "zh": "完成（{new_word_count} 个词，{sign}{delta}）。按 Ctrl+Z 撤销。",
    },

    # --- Boite de dialogue Modifier la selection ---
    "edit.title": {
        "fr": "MIrAI — Modifier la sélection",
        "en": "MIrAI — Edit selection",
        "es": "MIrAI — Modificar la selección",
        "pt": "MIrAI — Editar a seleção",
        "zh": "MIrAI — 编辑所选内容",
    },
    "edit.title_short": {
        "fr": "Modifier la sélection",
        "en": "Edit selection",
        "es": "Modificar la selección",
        "pt": "Editar a seleção",
        "zh": "编辑所选内容",
    },
    "edit.instructions_prompt": {
        "fr": "Saisissez vos instructions d'édition !",
        "en": "Enter your editing instructions!",
        "es": "¡Introduzca sus instrucciones de edición!",
        "pt": "Insira as suas instruções de edição!",
        "zh": "请输入您的编辑指令！",
    },
    "edit.intro": {
        "fr": (
            "Sélectionner une portion de texte à modifier... ou placer le curseur "
            "à l'emplacement où vous souhaitez insérer le nouveau texte"
        ),
        "en": (
            "Select a portion of text to edit... or place the cursor where you want "
            "to insert the new text"
        ),
        "es": (
            "Seleccione una parte del texto que desea modificar... o coloque el cursor "
            "donde quiera insertar el nuevo texto"
        ),
        "pt": (
            "Selecione um trecho de texto a editar... ou posicione o cursor onde "
            "deseja inserir o novo texto"
        ),
        "zh": "选择要修改的文本片段……或将光标放在要插入新文本的位置",
    },
    "edit.button": {
        "fr": "Editer avec l'IA",
        "en": "Edit with AI",
        "es": "Editar con IA",
        "pt": "Editar com IA",
        "zh": "用 AI 编辑",
    },
    "edit.open_prompt": {
        "fr": "Ouvrir prompt.txt",
        "en": "Open prompt.txt",
        "es": "Abrir prompt.txt",
        "pt": "Abrir prompt.txt",
        "zh": "打开 prompt.txt",
    },
    "edit.selection_prefix": {
        "fr": "Sélection {snippet}{warning}",
        "en": "Selection {snippet}{warning}",
        "es": "Selección {snippet}{warning}",
        "pt": "Seleção {snippet}{warning}",
        "zh": "所选内容 {snippet}{warning}",
    },
    "edit.warning_mixed_styles": {
        "fr": " ⚠ plusieurs styles fusionnés",
        "en": " ⚠ several styles merged",
        "es": " ⚠ varios estilos fusionados",
        "pt": " ⚠ vários estilos mesclados",
        "zh": " ⚠ 多个样式已合并",
    },
    "edit.prepare_suggestions": {
        "fr": "Mirai prépare des suggestions{dots}",
        "en": "Mirai is preparing suggestions{dots}",
        "es": "Mirai está preparando sugerencias{dots}",
        "pt": "Mirai está preparando sugestões{dots}",
        "zh": "Mirai 正在准备建议{dots}",
    },
    "edit.suggestions_plain": {
        "fr": "Suggestions",
        "en": "Suggestions",
        "es": "Sugerencias",
        "pt": "Sugestões",
        "zh": "建议",
    },
    "edit.generating": {
        "fr": "Génération en cours...",
        "en": "Generating...",
        "es": "Generando...",
        "pt": "Gerando...",
        "zh": "正在生成…",
    },
    "edit.suggest.1": {
        "fr": "Corrige l’orthographe et la grammaire.",
        "en": "Fix spelling and grammar.",
        "es": "Corrige la ortografía y la gramática.",
        "pt": "Corrija a ortografia e a gramática.",
        "zh": "修正拼写和语法。",
    },
    "edit.suggest.2": {
        "fr": "Reformule en style formel et concis.",
        "en": "Rewrite in a formal, concise style.",
        "es": "Reformula con un estilo formal y conciso.",
        "pt": "Reformule em estilo formal e conciso.",
        "zh": "改写为正式、简洁的风格。",
    },
    "edit.suggest.3": {
        "fr": "Simplifie pour un public non spécialiste.",
        "en": "Simplify for a non-specialist audience.",
        "es": "Simplifica para un público no especializado.",
        "pt": "Simplifique para um público não especialista.",
        "zh": "为非专业读者简化。",
    },
    "edit.suggest.4": {
        "fr": "Rends le texte plus clair avec des phrases courtes.",
        "en": "Make the text clearer with short sentences.",
        "es": "Haz el texto más claro con frases cortas.",
        "pt": "Deixe o texto mais claro com frases curtas.",
        "zh": "用短句让文本更清晰。",
    },
    "edit.suggest.5": {
        "fr": "Transforme en style administratif.",
        "en": "Convert to administrative style.",
        "es": "Transforma a estilo administrativo.",
        "pt": "Transforme em estilo administrativo.",
        "zh": "转换为公文体风格。",
    },
    "edit.suggest.6": {
        "fr": "Rends la formulation plus positive et professionnelle.",
        "en": "Make the wording more positive and professional.",
        "es": "Haz la redacción más positiva y profesional.",
        "pt": "Deixe a redação mais positiva e profissional.",
        "zh": "让措辞更积极、更专业。",
    },
    "edit.suggest.7": {
        "fr": "Réorganise pour améliorer la logique et la structure.",
        "en": "Reorganize to improve logic and structure.",
        "es": "Reorganiza para mejorar la lógica y la estructura.",
        "pt": "Reorganize para melhorar a lógica e a estrutura.",
        "zh": "重新组织以改善逻辑和结构。",
    },
    "edit.suggest.8": {
        "fr": "Supprime les répétitions et les tournures lourdes.",
        "en": "Remove repetitions and clunky phrasing.",
        "es": "Elimina repeticiones y expresiones pesadas.",
        "pt": "Remova repetições e construções pesadas.",
        "zh": "删除重复和冗长表达。",
    },
    "edit.suggest.9": {
        "fr": "Rends le texte plus convaincant sans changer le sens.",
        "en": "Make the text more convincing without changing its meaning.",
        "es": "Haz el texto más convincente sin cambiar el sentido.",
        "pt": "Deixe o texto mais convincente sem mudar o sentido.",
        "zh": "在不改变含义的前提下让文本更有说服力。",
    },
    "edit.suggest.10": {
        "fr": "Résume le contenu en gardant l’essentiel.",
        "en": "Summarize the content while keeping the essentials.",
        "es": "Resume el contenido manteniendo lo esencial.",
        "pt": "Resuma o conteúdo mantendo o essencial.",
        "zh": "保留要点并概括内容。",
    },

    # --- Boite de dialogue Transformer les cellules (Calc) ---
    "calc.title": {
        "fr": "MIrAI — Transformer les cellules",
        "en": "MIrAI — Transform cells",
        "es": "MIrAI — Transformar las celdas",
        "pt": "MIrAI — Transformar as células",
        "zh": "MIrAI — 转换单元格",
    },
    "calc.ok_button": {
        "fr": "Transformer",
        "en": "Transform",
        "es": "Transformar",
        "pt": "Transformar",
        "zh": "转换",
    },
    "calc.title_suffix": {
        "fr": " les cellules",
        "en": " cells",
        "es": " las celdas",
        "pt": " as células",
        "zh": " 单元格",
    },
    "calc.out_new_col": {
        "fr": "  →  nouvelle colonne « {name} » (col. {letter})",
        "en": "  →  new column \"{name}\" (col. {letter})",
        "es": "  →  nueva columna «{name}» (col. {letter})",
        "pt": "  →  nova coluna «{name}» (col. {letter})",
        "zh": "  →  新列“{name}”（列 {letter}）",
    },
    "calc.out_col": {
        "fr": "  →  col. {letter}",
        "en": "  →  col. {letter}",
        "es": "  →  col. {letter}",
        "pt": "  →  col. {letter}",
        "zh": "  →  列 {letter}",
    },
    "calc.out_col_header": {
        "fr": " « {header} »",
        "en": " \"{header}\"",
        "es": " «{header}»",
        "pt": " «{header}»",
        "zh": "“{header}”",
    },
    "calc.selection_one": {
        "fr": "{n} cellule sélectionnée ({ref})",
        "en": "{n} selected cell ({ref})",
        "es": "{n} celda seleccionada ({ref})",
        "pt": "{n} célula selecionada ({ref})",
        "zh": "已选择 {n} 个单元格（{ref}）",
    },
    "calc.selection_many": {
        "fr": "{n} cellules sélectionnées ({ref})",
        "en": "{n} selected cells ({ref})",
        "es": "{n} celdas seleccionadas ({ref})",
        "pt": "{n} células selecionadas ({ref})",
        "zh": "已选择 {n} 个单元格（{ref}）",
    },
    # --- Assistant Formule (Calc) : lignes de statut ---
    "calc.formula.detail": {
        "fr": "Formule : {formula}",
        "en": "Formula: {formula}",
        "es": "Fórmula: {formula}",
        "pt": "Fórmula: {formula}",
        "zh": "公式：{formula}",
    },
    "calc.formula.detail_explained": {
        "fr": "Formule : {formula}\n\n{explanation}",
        "en": "Formula: {formula}\n\n{explanation}",
        "es": "Fórmula: {formula}\n\n{explanation}",
        "pt": "Fórmula: {formula}\n\n{explanation}",
        "zh": "公式：{formula}\n\n{explanation}",
    },
    "calc.formula.click_apply": {
        "fr": "── Cliquez Appliquer pour insérer ──",
        "en": "── Click Apply to insert ──",
        "es": "── Haga clic en Aplicar para insertar ──",
        "pt": "── Clique em Aplicar para inserir ──",
        "zh": "── 点击“应用”以插入 ──",
    },
    "calc.formula.none_generated": {
        "fr": "⚠ Aucune formule générée",
        "en": "⚠ No formula generated",
        "es": "⚠ No se ha generado ninguna fórmula",
        "pt": "⚠ Nenhuma fórmula gerada",
        "zh": "⚠ 未生成公式",
    },
    "calc.formula.none_to_apply": {
        "fr": "⚠ Aucune formule à appliquer",
        "en": "⚠ No formula to apply",
        "es": "⚠ No hay ninguna fórmula que aplicar",
        "pt": "⚠ Nenhuma fórmula para aplicar",
        "zh": "⚠ 没有可应用的公式",
    },
    "calc.formula.applied": {
        "fr": "✓ Appliqué : {formula}",
        "en": "✓ Applied: {formula}",
        "es": "✓ Aplicado: {formula}",
        "pt": "✓ Aplicado: {formula}",
        "zh": "✓ 已应用：{formula}",
    },
    "calc.formula.filled_down": {
        "fr": "↓ Répliqué sur {count} lignes",
        "en": "↓ Filled down over {count} rows",
        "es": "↓ Rellenado en {count} filas",
        "pt": "↓ Preenchido em {count} linhas",
        "zh": "↓ 已向下填充 {count} 行",
    },
    "calc.formula.error_line": {
        "fr": "⚠ Erreur : {err}",
        "en": "⚠ Error: {err}",
        "es": "⚠ Error: {err}",
        "pt": "⚠ Erro: {err}",
        "zh": "⚠ 错误：{err}",
    },
    "calc.suggest.1": {
        "fr": "Traduire en anglais",
        "en": "Translate to English",
        "es": "Traducir al inglés",
        "pt": "Traduzir para inglês",
        "zh": "翻译成英文",
    },
    "calc.suggest.2": {
        "fr": "Mettre la première lettre en majuscule",
        "en": "Capitalize the first letter",
        "es": "Poner la primera letra en mayúscula",
        "pt": "Colocar a primeira letra em maiúscula",
        "zh": "将首字母大写",
    },
    "calc.suggest.3": {
        "fr": "Résumer en une phrase courte",
        "en": "Summarize in one short sentence",
        "es": "Resumir en una frase corta",
        "pt": "Resumir em uma frase curta",
        "zh": "用一句话简要概括",
    },
    "calc.suggest.4": {
        "fr": "Extraire les mots-clés (séparés par des virgules)",
        "en": "Extract keywords (comma-separated)",
        "es": "Extraer las palabras clave (separadas por comas)",
        "pt": "Extrair as palavras-chave (separadas por vírgulas)",
        "zh": "提取关键词（逗号分隔）",
    },
    "calc.suggest.5": {
        "fr": "Classifier comme Positif / Négatif / Neutre",
        "en": "Classify as Positive / Negative / Neutral",
        "es": "Clasificar como Positivo / Negativo / Neutro",
        "pt": "Classificar como Positivo / Negativo / Neutro",
        "zh": "分类为 正面 / 负面 / 中性",
    },
    "calc.suggest.6": {
        "fr": "Corriger l'orthographe et la grammaire",
        "en": "Fix spelling and grammar",
        "es": "Corregir la ortografía y la gramática",
        "pt": "Corrigir a ortografia e a gramática",
        "zh": "修正拼写和语法",
    },
    "calc.suggest.7": {
        "fr": "Normaliser le format (ex: prénom nom → PRÉNOM NOM)",
        "en": "Normalize the format (e.g. first last → FIRST LAST)",
        "es": "Normalizar el formato (ej.: nombre apellido → NOMBRE APELLIDO)",
        "pt": "Normalizar o formato (ex.: nome sobrenome → NOME SOBRENOME)",
        "zh": "规范化格式（例如：zhang wei → ZHANG WEI）",
    },
    "calc.suggest.8": {
        "fr": "Extraire le premier nombre trouvé",
        "en": "Extract the first number found",
        "es": "Extraer el primer número encontrado",
        "pt": "Extrair o primeiro número encontrado",
        "zh": "提取找到的第一个数字",
    },
    "calc.suggest.9": {
        "fr": "Détecter la langue (ex: FR / EN / DE)",
        "en": "Detect the language (e.g. FR / EN / DE)",
        "es": "Detectar el idioma (ej.: FR / EN / DE)",
        "pt": "Detectar o idioma (ex.: FR / EN / DE)",
        "zh": "检测语言（例如 FR / EN / DE）",
    },
    "calc.suggest.10": {
        "fr": "Reformuler de façon plus formelle",
        "en": "Rephrase more formally",
        "es": "Reformular de forma más formal",
        "pt": "Reformular de forma mais formal",
        "zh": "以更正式的方式改写",
    },

    # --- Assistant Formule ---
    "formula.title": {
        "fr": "MIrAI — Assistant Formule",
        "en": "MIrAI — Formula assistant",
        "es": "MIrAI — Asistente de fórmulas",
        "pt": "MIrAI — Assistente de fórmulas",
        "zh": "MIrAI — 公式助手",
    },
    "formula.header": {
        "fr": "🤖 MIrAI — Assistant Formule",
        "en": "🤖 MIrAI — Formula assistant",
        "es": "🤖 MIrAI — Asistente de fórmulas",
        "pt": "🤖 MIrAI — Assistente de fórmulas",
        "zh": "🤖 MIrAI — 公式助手",
    },
    "formula.request_label": {
        "fr": "Votre demande :",
        "en": "Your request:",
        "es": "Su solicitud:",
        "pt": "Sua solicitação:",
        "zh": "您的请求：",
    },
    "formula.preview": {
        "fr": "⚡ Prévisualiser",
        "en": "⚡ Preview",
        "es": "⚡ Previsualizar",
        "pt": "⚡ Pré-visualizar",
        "zh": "⚡ 预览",
    },
    "formula.apply": {
        "fr": "✓ Appliquer",
        "en": "✓ Apply",
        "es": "✓ Aplicar",
        "pt": "✓ Aplicar",
        "zh": "✓ 应用",
    },
    "formula.detail_placeholder": {
        "fr": "La formule et son explication apparaîtront ici après la prévisualisation.",
        "en": "The formula and its explanation will appear here after previewing.",
        "es": "La fórmula y su explicación aparecerán aquí tras la previsualización.",
        "pt": "A fórmula e a sua explicação aparecerão aqui após a pré-visualização.",
        "zh": "预览后，公式及其说明将显示在这里。",
    },
    "formula.no_context": {
        "fr": "Aucun contexte disponible",
        "en": "No context available",
        "es": "No hay contexto disponible",
        "pt": "Nenhum contexto disponível",
        "zh": "没有可用的上下文",
    },
    "formula.conversation": {
        "fr": "Conversation :",
        "en": "Conversation:",
        "es": "Conversación:",
        "pt": "Conversa:",
        "zh": "对话：",
    },
    "formula.clear": {
        "fr": "Vider…",
        "en": "Clear…",
        "es": "Vaciar…",
        "pt": "Limpar…",
        "zh": "清空…",
    },
    "formula.open_prompts": {
        "fr": "Ouvrir prompts…",
        "en": "Open prompts…",
        "es": "Abrir prompts…",
        "pt": "Abrir prompts…",
        "zh": "打开提示词…",
    },
    "formula.thinking": {
        "fr": "⏳ Mirai réfléchit...",
        "en": "⏳ Mirai is thinking...",
        "es": "⏳ Mirai está pensando...",
        "pt": "⏳ Mirai está pensando...",
        "zh": "⏳ Mirai 正在思考…",
    },
    "formula.generating": {
        "fr": "Mirai génère la formule...",
        "en": "Mirai is generating the formula...",
        "es": "Mirai está generando la fórmula...",
        "pt": "Mirai está gerando a fórmula...",
        "zh": "Mirai 正在生成公式…",
    },
    "formula.applying": {
        "fr": "Application de la formule...",
        "en": "Applying the formula...",
        "es": "Aplicando la fórmula...",
        "pt": "Aplicando a fórmula...",
        "zh": "正在应用公式…",
    },
    "formula.clear_history_question": {
        "fr": "Vider l'historique des demandes ?",
        "en": "Clear the request history?",
        "es": "¿Vaciar el historial de solicitudes?",
        "pt": "Limpar o histórico de solicitações?",
        "zh": "清空请求历史记录？",
    },

    # --- Assistant d'enrolement ---
    "enroll.welcome": {
        "fr": "Bienvenue dans IA'ssistant by MIrAI",
        "en": "Welcome to IA'ssistant by MIrAI",
        "es": "Bienvenido a IA'ssistant by MIrAI",
        "pt": "Bem-vindo ao IA'ssistant by MIrAI",
        "zh": "欢迎使用 IA'ssistant by MIrAI",
    },
    "enroll.step1_text": {
        "fr": (
            "Votre assistant IA pour LibreOffice est presque prêt !\n\n"
            "IA'ssistant vous aide à rédiger, reformuler, résumer\n"
            "et enrichir vos documents en toute simplicité.\n\n"
            "Pour activer les fonctionnalités IA, une courte\n"
            "procédure d'enrôlement sécurisé est nécessaire.\n\n"
            "Cela ne prend que quelques secondes."
        ),
        "en": (
            "Your AI assistant for LibreOffice is almost ready!\n\n"
            "IA'ssistant helps you write, rephrase, summarize\n"
            "and enrich your documents with ease.\n\n"
            "To enable the AI features, a short secure\n"
            "enrollment procedure is required.\n\n"
            "It only takes a few seconds."
        ),
        "es": (
            "¡Su asistente de IA para LibreOffice está casi listo!\n\n"
            "IA'ssistant le ayuda a redactar, reformular, resumir\n"
            "y enriquecer sus documentos con total facilidad.\n\n"
            "Para activar las funciones de IA se requiere un breve\n"
            "procedimiento de registro seguro.\n\n"
            "Solo tarda unos segundos."
        ),
        "pt": (
            "O seu assistente de IA para o LibreOffice está quase pronto!\n\n"
            "O IA'ssistant ajuda a redigir, reformular, resumir\n"
            "e enriquecer os seus documentos com toda a facilidade.\n\n"
            "Para ativar os recursos de IA é necessário um breve\n"
            "procedimento de registo seguro.\n\n"
            "Leva apenas alguns segundos."
        ),
        "zh": (
            "您的 LibreOffice AI 助手即将就绪！\n\n"
            "IA'ssistant 帮助您轻松撰写、改写、摘要\n"
            "并丰富您的文档。\n\n"
            "要启用 AI 功能，需要进行简短的\n"
            "安全注册流程。\n\n"
            "只需几秒钟。"
        ),
    },
    "enroll.start": {
        "fr": "Commencer",
        "en": "Start",
        "es": "Comenzar",
        "pt": "Começar",
        "zh": "开始",
    },
    "enroll.later": {
        "fr": "Plus tard",
        "en": "Later",
        "es": "Más tarde",
        "pt": "Mais tarde",
        "zh": "稍后",
    },
    "enroll.step1_label": {
        "fr": "Étape 1/5 — Présentation",
        "en": "Step 1/5 — Introduction",
        "es": "Paso 1/5 — Presentación",
        "pt": "Etapa 1/5 — Apresentação",
        "zh": "第 1/5 步 — 简介",
    },
    "enroll.step2_title": {
        "fr": "Connexion sécurisée",
        "en": "Secure connection",
        "es": "Conexión segura",
        "pt": "Conexão segura",
        "zh": "安全连接",
    },
    "enroll.step2_text": {
        "fr": (
            "Cliquez sur « Ouvrir le navigateur » pour vous connecter.\n\n"
            "  • Votre navigateur s'ouvrira sur la page de connexion MIrAI\n"
            "  • Après connexion, revenez dans LibreOffice\n"
            "  • L'enrôlement se fera automatiquement\n\n"
            "Vos données restent protégées :\n"
            "aucun mot de passe n'est stocké par le plugin."
        ),
        "en": (
            "Click \"Open browser\" to sign in.\n\n"
            "  • Your browser will open the MIrAI sign-in page\n"
            "  • After signing in, come back to LibreOffice\n"
            "  • Enrollment happens automatically\n\n"
            "Your data stays protected:\n"
            "the plugin stores no password."
        ),
        "es": (
            "Haga clic en «Abrir el navegador» para iniciar sesión.\n\n"
            "  • Su navegador se abrirá en la página de inicio de sesión de MIrAI\n"
            "  • Tras iniciar sesión, vuelva a LibreOffice\n"
            "  • El registro se realizará automáticamente\n\n"
            "Sus datos permanecen protegidos:\n"
            "el complemento no almacena ninguna contraseña."
        ),
        "pt": (
            "Clique em «Abrir o navegador» para iniciar sessão.\n\n"
            "  • O navegador abrirá a página de início de sessão do MIrAI\n"
            "  • Após iniciar sessão, volte ao LibreOffice\n"
            "  • O registo é feito automaticamente\n\n"
            "Os seus dados permanecem protegidos:\n"
            "o suplemento não guarda qualquer palavra-passe."
        ),
        "zh": (
            "点击「打开浏览器」进行登录。\n\n"
            "  • 浏览器将打开 MIrAI 登录页面\n"
            "  • 登录后请返回 LibreOffice\n"
            "  • 注册将自动完成\n\n"
            "您的数据始终受到保护：\n"
            "插件不会存储任何密码。"
        ),
    },
    "enroll.open_browser": {
        "fr": "Ouvrir le navigateur",
        "en": "Open browser",
        "es": "Abrir el navegador",
        "pt": "Abrir o navegador",
        "zh": "打开浏览器",
    },
    "enroll.step2_label": {
        "fr": "Étape 2/5 — Authentification",
        "en": "Step 2/5 — Authentication",
        "es": "Paso 2/5 — Autenticación",
        "pt": "Etapa 2/5 — Autenticação",
        "zh": "第 2/5 步 — 身份验证",
    },
    "enroll.step4_label": {
        "fr": "Étape 4/5 — Connexion",
        "en": "Step 4/5 — Sign-in",
        "es": "Paso 4/5 — Conexión",
        "pt": "Etapa 4/5 — Conexão",
        "zh": "第 4/5 步 — 登录",
    },
    "enroll.step5_enroll_label": {
        "fr": "Étape 5/5 — Enrôlement",
        "en": "Step 5/5 — Enrollment",
        "es": "Paso 5/5 — Registro",
        "pt": "Etapa 5/5 — Registo",
        "zh": "第 5/5 步 — 注册",
    },
    "enroll.step5_done_label": {
        "fr": "Étape 5/5 — Terminé",
        "en": "Step 5/5 — Done",
        "es": "Paso 5/5 — Completado",
        "pt": "Etapa 5/5 — Concluído",
        "zh": "第 5/5 步 — 完成",
    },
    "enroll.step5_error_label": {
        "fr": "Étape 5/5 — Erreur",
        "en": "Step 5/5 — Error",
        "es": "Paso 5/5 — Error",
        "pt": "Etapa 5/5 — Erro",
        "zh": "第 5/5 步 — 错误",
    },
    "enroll.auth_wait_title": {
        "fr": "Connexion en cours...",
        "en": "Signing in...",
        "es": "Iniciando sesión...",
        "pt": "A iniciar sessão...",
        "zh": "正在登录……",
    },
    "enroll.auth_wait_text": {
        "fr": (
            "Votre navigateur est ouvert sur la page de connexion.\n\n"
            "Connectez-vous puis revenez dans LibreOffice."
        ),
        "en": (
            "Your browser is open on the sign-in page.\n\n"
            "Sign in, then come back to LibreOffice."
        ),
        "es": (
            "Su navegador está abierto en la página de inicio de sesión.\n\n"
            "Inicie sesión y vuelva a LibreOffice."
        ),
        "pt": (
            "O seu navegador está aberto na página de início de sessão.\n\n"
            "Inicie sessão e volte ao LibreOffice."
        ),
        "zh": (
            "您的浏览器已打开登录页面。\n\n"
            "请登录后返回 LibreOffice。"
        ),
    },
    "enroll.auth_waiting": {
        "fr": (
            "En attente de la connexion{dots}\n\n"
            "Connectez-vous dans le navigateur puis revenez."
        ),
        "en": (
            "Waiting for sign-in{dots}\n\n"
            "Sign in through the browser, then come back."
        ),
        "es": (
            "Esperando el inicio de sesión{dots}\n\n"
            "Inicie sesión en el navegador y vuelva."
        ),
        "pt": (
            "A aguardar o início de sessão{dots}\n\n"
            "Inicie sessão no navegador e volte."
        ),
        "zh": (
            "正在等待登录{dots}\n\n"
            "请在浏览器中登录后返回。"
        ),
    },
    "enroll.auth_progress": {
        "fr": "Authentification Keycloak{dots}",
        "en": "Keycloak authentication{dots}",
        "es": "Autenticación Keycloak{dots}",
        "pt": "Autenticação Keycloak{dots}",
        "zh": "Keycloak 身份验证{dots}",
    },
    "enroll.cancelling": {
        "fr": "Annulation...",
        "en": "Cancelling...",
        "es": "Cancelando...",
        "pt": "A cancelar...",
        "zh": "正在取消……",
    },
    "enroll.cancelled_title": {
        "fr": "Connexion annulée",
        "en": "Sign-in cancelled",
        "es": "Inicio de sesión cancelado",
        "pt": "Início de sessão cancelado",
        "zh": "登录已取消",
    },
    "enroll.cancelled_text": {
        "fr": "L'authentification a été annulée.\n\nVous pouvez réessayer via le menu MIrAI.",
        "en": "Authentication was cancelled.\n\nYou can try again from the MIrAI menu.",
        "es": "La autenticación se ha cancelado.\n\nPuede volver a intentarlo desde el menú MIrAI.",
        "pt": "A autenticação foi cancelada.\n\nPode tentar novamente no menu MIrAI.",
        "zh": "身份验证已取消。\n\n您可以从 MIrAI 菜单重试。",
    },
    "enroll.failed_conn_title": {
        "fr": "Connexion échouée",
        "en": "Sign-in failed",
        "es": "Error de inicio de sesión",
        "pt": "Falha no início de sessão",
        "zh": "登录失败",
    },
    "enroll.timeout_text": {
        "fr": "Délai dépassé. Vérifiez la redirection et réessayez.",
        "en": "Timed out. Check the redirect and try again.",
        "es": "Se agotó el tiempo. Compruebe la redirección e inténtelo de nuevo.",
        "pt": "Tempo esgotado. Verifique o redirecionamento e tente novamente.",
        "zh": "已超时。请检查重定向并重试。",
    },
    "enroll.error_config_text": {
        "fr": "Erreur : {error}. Vérifiez la configuration.",
        "en": "Error: {error}. Check the configuration.",
        "es": "Error: {error}. Compruebe la configuración.",
        "pt": "Erro: {error}. Verifique a configuração.",
        "zh": "错误：{error}。请检查配置。",
    },
    "enroll.unknown_error": {
        "fr": "Erreur inconnue",
        "en": "Unknown error",
        "es": "Error desconocido",
        "pt": "Erro desconhecido",
        "zh": "未知错误",
    },
    "enroll.not_confirmed": {
        "fr": "Non confirmé par le serveur",
        "en": "Not confirmed by the server",
        "es": "No confirmado por el servidor",
        "pt": "Não confirmado pelo servidor",
        "zh": "服务器未确认",
    },
    "enroll.enrolling_title": {
        "fr": "Enrôlement en cours...",
        "en": "Enrolling...",
        "es": "Registrando...",
        "pt": "A registar...",
        "zh": "正在注册……",
    },
    "enroll.enrolling_text": {
        "fr": "Enregistrement de votre poste auprès du service MIrAI...",
        "en": "Registering your device with the MIrAI service...",
        "es": "Registrando su equipo en el servicio MIrAI...",
        "pt": "A registar o seu dispositivo no serviço MIrAI...",
        "zh": "正在向 MIrAI 服务注册您的设备……",
    },
    "enroll.enrolling_progress": {
        "fr": "Enregistrement en cours{dots}",
        "en": "Registration in progress{dots}",
        "es": "Registro en curso{dots}",
        "pt": "Registo em curso{dots}",
        "zh": "正在注册{dots}",
    },
    "enroll.done_title": {
        "fr": "Enrôlement terminé !",
        "en": "Enrollment complete!",
        "es": "¡Registro completado!",
        "pt": "Registo concluído!",
        "zh": "注册完成！",
    },
    "enroll.done_text": {
        "fr": (
            "L'IA est intégrée directement dans vos documents\n"
            "Writer et Calc, accessible depuis le menu MIrAI.\n\n"
            "Writer :\n"
            "  • Étendre — prolonger votre texte avec l'IA\n"
            "  • Modifier — reformuler ou corriger une sélection\n"
            "  • Résumer — condenser un passage\n"
            "  • Simplifier — rendre un texte plus accessible\n\n"
            "Calc :\n"
            "  • Transformer — appliquer une consigne à chaque cellule\n"
            "  • Formule IA — générer une formule par description\n"
            "  • Analyser — obtenir une synthèse de vos données\n"
            "  • =PROMPT() — interroger l'IA dans une cellule\n\n\n"
            "       Menu MIrAI → 📚 Documentation pour en savoir plus."
        ),
        "en": (
            "The AI is built right into your Writer and Calc\n"
            "documents, available from the MIrAI menu.\n\n"
            "Writer:\n"
            "  • Extend — lengthen your text with the AI\n"
            "  • Edit — rephrase or correct a selection\n"
            "  • Summarize — condense a passage\n"
            "  • Simplify — make a text more accessible\n\n"
            "Calc:\n"
            "  • Transform — apply an instruction to each cell\n"
            "  • AI formula — generate a formula from a description\n"
            "  • Analyze — get a summary of your data\n"
            "  • =PROMPT() — query the AI inside a cell\n\n\n"
            "       MIrAI menu → 📚 Documentation to learn more."
        ),
        "es": (
            "La IA está integrada directamente en sus documentos\n"
            "Writer y Calc, accesible desde el menú MIrAI.\n\n"
            "Writer:\n"
            "  • Ampliar — prolongar su texto con la IA\n"
            "  • Modificar — reformular o corregir una selección\n"
            "  • Resumir — condensar un pasaje\n"
            "  • Simplificar — hacer un texto más accesible\n\n"
            "Calc:\n"
            "  • Transformar — aplicar una consigna a cada celda\n"
            "  • Fórmula IA — generar una fórmula por descripción\n"
            "  • Analizar — obtener una síntesis de sus datos\n"
            "  • =PROMPT() — consultar la IA en una celda\n\n\n"
            "       Menú MIrAI → 📚 Documentación para saber más."
        ),
        "pt": (
            "A IA está integrada diretamente nos seus documentos\n"
            "Writer e Calc, acessível no menu MIrAI.\n\n"
            "Writer:\n"
            "  • Expandir — prolongar o seu texto com a IA\n"
            "  • Modificar — reformular ou corrigir uma seleção\n"
            "  • Resumir — condensar uma passagem\n"
            "  • Simplificar — tornar um texto mais acessível\n\n"
            "Calc:\n"
            "  • Transformar — aplicar uma instrução a cada célula\n"
            "  • Fórmula IA — gerar uma fórmula por descrição\n"
            "  • Analisar — obter uma síntese dos seus dados\n"
            "  • =PROMPT() — consultar a IA numa célula\n\n\n"
            "       Menu MIrAI → 📚 Documentação para saber mais."
        ),
        "zh": (
            "AI 已直接集成到您的 Writer 和 Calc 文档中，\n"
            "可通过 MIrAI 菜单使用。\n\n"
            "Writer：\n"
            "  • 扩展 — 用 AI 延长您的文本\n"
            "  • 修改 — 改写或修正选中的内容\n"
            "  • 摘要 — 浓缩一段文字\n"
            "  • 简化 — 让文本更易读\n\n"
            "Calc：\n"
            "  • 转换 — 对每个单元格应用指令\n"
            "  • AI 公式 — 根据描述生成公式\n"
            "  • 分析 — 获取数据摘要\n"
            "  • =PROMPT() — 在单元格中查询 AI\n\n\n"
            "       MIrAI 菜单 → 📚 文档以了解更多。"
        ),
    },
    "enroll.done_button": {
        "fr": "🚀 Commencer à utiliser",
        "en": "🚀 Start using",
        "es": "🚀 Empezar a usar",
        "pt": "🚀 Começar a usar",
        "zh": "🚀 开始使用",
    },
    "enroll.failed_title": {
        "fr": "Enrôlement échoué",
        "en": "Enrollment failed",
        "es": "Registro fallido",
        "pt": "Registo falhado",
        "zh": "注册失败",
    },
    "enroll.failed_text": {
        "fr": (
            "L'enrôlement a échoué.\n"
            "Raison : {reason}\n\n"
            "Consultez le menu MIrAI → 📚 Documentation\n"
            "pour obtenir de l'aide."
        ),
        "en": (
            "Enrollment failed.\n"
            "Reason: {reason}\n\n"
            "See the MIrAI menu → 📚 Documentation\n"
            "for help."
        ),
        "es": (
            "El registro ha fallado.\n"
            "Motivo: {reason}\n\n"
            "Consulte el menú MIrAI → 📚 Documentación\n"
            "para obtener ayuda."
        ),
        "pt": (
            "O registo falhou.\n"
            "Motivo: {reason}\n\n"
            "Consulte o menu MIrAI → 📚 Documentação\n"
            "para obter ajuda."
        ),
        "zh": (
            "注册失败。\n"
            "原因：{reason}\n\n"
            "请查看 MIrAI 菜单 → 📚 文档\n"
            "以获取帮助。"
        ),
    },

    # --- Messages transverses ---
    "msg.connection_required_title": {
        "fr": "Connexion MIrAI requise",
        "en": "MIrAI connection required",
        "es": "Se requiere conexión con MIrAI",
        "pt": "Conexão MIrAI necessária",
        "zh": "需要连接 MIrAI",
    },
    "msg.continue_question": {
        "fr": "Voulez-vous continuer ?",
        "en": "Do you want to continue?",
        "es": "¿Desea continuar?",
        "pt": "Deseja continuar?",
        "zh": "是否继续？",
    },
    "msg.connection_required_body": {
        "fr": (
            "Vous allez être redirigé vers la page de connexion MIrAI dans votre navigateur.\n\n"
            "Après la connexion, revenez à LibreOffice.\n\n"
            "Voulez-vous continuer ?"
        ),
        "en": (
            "You will be redirected to the MIrAI sign-in page in your browser.\n\n"
            "After signing in, come back to LibreOffice.\n\n"
            "Do you want to continue?"
        ),
        "es": (
            "Se le redirigirá a la página de inicio de sesión de MIrAI en su navegador.\n\n"
            "Tras iniciar sesión, vuelva a LibreOffice.\n\n"
            "¿Desea continuar?"
        ),
        "pt": (
            "Será redirecionado para a página de início de sessão do MIrAI no seu navegador.\n\n"
            "Após iniciar sessão, volte ao LibreOffice.\n\n"
            "Deseja continuar?"
        ),
        "zh": (
            "您将被重定向到浏览器中的 MIrAI 登录页面。\n\n"
            "登录后，请返回 LibreOffice。\n\n"
            "是否继续？"
        ),
    },
    "msg.connection_redirect_short": {
        "fr": (
            "Vous allez être redirigé vers la page de connexion MIrAI.\n\n"
            "Voulez-vous continuer ?"
        ),
        "en": (
            "You will be redirected to the MIrAI sign-in page.\n\n"
            "Do you want to continue?"
        ),
        "es": (
            "Se le redirigirá a la página de inicio de sesión de MIrAI.\n\n"
            "¿Desea continuar?"
        ),
        "pt": (
            "Será redirecionado para a página de início de sessão do MIrAI.\n\n"
            "Deseja continuar?"
        ),
        "zh": (
            "您将被重定向到 MIrAI 登录页面。\n\n"
            "是否继续？"
        ),
    },
    "msg.kc_invalid_title": {
        "fr": "Configuration Keycloak invalide",
        "en": "Invalid Keycloak configuration",
        "es": "Configuración de Keycloak no válida",
        "pt": "Configuração do Keycloak inválida",
        "zh": "Keycloak 配置无效",
    },
    "msg.kc_invalid_body": {
        "fr": "redirect_uri n'est pas autorisé.\n\nVérifiez keycloak_allowed_redirect_uri.",
        "en": "redirect_uri is not allowed.\n\nCheck keycloak_allowed_redirect_uri.",
        "es": "redirect_uri no está permitido.\n\nCompruebe keycloak_allowed_redirect_uri.",
        "pt": "redirect_uri não é permitido.\n\nVerifique keycloak_allowed_redirect_uri.",
        "zh": "redirect_uri 不被允许。\n\n请检查 keycloak_allowed_redirect_uri。",
    },
    "msg.kc_incomplete_title": {
        "fr": "Configuration Keycloak incomplète",
        "en": "Incomplete Keycloak configuration",
        "es": "Configuración de Keycloak incompleta",
        "pt": "Configuração do Keycloak incompleta",
        "zh": "Keycloak 配置不完整",
    },
    "msg.kc_endpoints_missing": {
        "fr": (
            "Impossible d'ouvrir la page d'authentification : endpoints Keycloak manquants.\n\n"
            "Vérifiez keycloakIssuerUrl / keycloakRealm."
        ),
        "en": (
            "Cannot open the authentication page: Keycloak endpoints are missing.\n\n"
            "Check keycloakIssuerUrl / keycloakRealm."
        ),
        "es": (
            "No se puede abrir la página de autenticación: faltan los endpoints de Keycloak.\n\n"
            "Compruebe keycloakIssuerUrl / keycloakRealm."
        ),
        "pt": (
            "Não é possível abrir a página de autenticação: faltam os endpoints do Keycloak.\n\n"
            "Verifique keycloakIssuerUrl / keycloakRealm."
        ),
        "zh": "无法打开身份验证页面：缺少 Keycloak 端点。\n\n请检查 keycloakIssuerUrl / keycloakRealm。",
    },
    "msg.kc_client_id_missing": {
        "fr": "Impossible d'ouvrir la page d'authentification : client_id manquant.",
        "en": "Cannot open the authentication page: client_id is missing.",
        "es": "No se puede abrir la página de autenticación: falta client_id.",
        "pt": "Não é possível abrir a página de autenticação: falta o client_id.",
        "zh": "无法打开身份验证页面：缺少 client_id。",
    },
    "msg.kc_redirect_missing": {
        "fr": (
            "Impossible d'ouvrir la page d'authentification : redirect_uri manquant.\n\n"
            "Exemple : http://localhost:28443/callback"
        ),
        "en": (
            "Cannot open the authentication page: redirect_uri is missing.\n\n"
            "Example: http://localhost:28443/callback"
        ),
        "es": (
            "No se puede abrir la página de autenticación: falta redirect_uri.\n\n"
            "Ejemplo: http://localhost:28443/callback"
        ),
        "pt": (
            "Não é possível abrir a página de autenticação: falta o redirect_uri.\n\n"
            "Exemplo: http://localhost:28443/callback"
        ),
        "zh": "无法打开身份验证页面：缺少 redirect_uri。\n\n示例：http://localhost:28443/callback",
    },
    "msg.kc_expired_title": {
        "fr": "Connexion expirée",
        "en": "Connection expired",
        "es": "Sesión expirada",
        "pt": "Conexão expirada",
        "zh": "连接已过期",
    },
    "msg.kc_expired_body": {
        "fr": (
            "Le login Keycloak a expiré avant le retour navigateur.\n\n"
            "Redirection attendue:\n{redirect_uri}\n\n"
            "Vérifiez la redirection et relancez Login."
        ),
        "en": (
            "The Keycloak login expired before the browser returned.\n\n"
            "Expected redirect:\n{redirect_uri}\n\n"
            "Check the redirect and start Login again."
        ),
        "es": (
            "El inicio de sesión de Keycloak caducó antes de volver del navegador.\n\n"
            "Redirección esperada:\n{redirect_uri}\n\n"
            "Compruebe la redirección y vuelva a iniciar Login."
        ),
        "pt": (
            "O início de sessão do Keycloak expirou antes do retorno do navegador.\n\n"
            "Redirecionamento esperado:\n{redirect_uri}\n\n"
            "Verifique o redirecionamento e reinicie o Login."
        ),
        "zh": (
            "浏览器返回前 Keycloak 登录已过期。\n\n"
            "预期重定向：\n{redirect_uri}\n\n"
            "请检查重定向并重新启动登录。"
        ),
    },
    "msg.token_invalid_title": {
        "fr": "Token invalide",
        "en": "Invalid token",
        "es": "Token no válido",
        "pt": "Token inválido",
        "zh": "令牌无效",
    },
    "msg.token_invalid_body": {
        "fr": "Votre token n'est plus valide.\n\nVoulez-vous ouvrir les préférences pour le vérifier ?",
        "en": "Your token is no longer valid.\n\nDo you want to open the preferences to check it?",
        "es": "Su token ya no es válido.\n\n¿Desea abrir las preferencias para comprobarlo?",
        "pt": "O seu token já não é válido.\n\nDeseja abrir as preferências para o verificar?",
        "zh": "您的令牌已失效。\n\n是否打开首选项进行检查？",
    },
    "msg.quota_title": {
        "fr": "Quota de requêtes atteint",
        "en": "Request quota reached",
        "es": "Cuota de solicitudes alcanzada",
        "pt": "Cota de solicitações atingida",
        "zh": "请求配额已达上限",
    },
    "msg.quota_body": {
        "fr": (
            "Le quota de requêtes vers l'assistant IA est atteint pour le moment.\n\n"
            "Merci de réessayer dans {delay}."
        ),
        "en": (
            "The request quota for the AI assistant has been reached for now.\n\n"
            "Please try again in {delay}."
        ),
        "es": (
            "Se ha alcanzado por ahora la cuota de solicitudes al asistente de IA.\n\n"
            "Vuelva a intentarlo en {delay}."
        ),
        "pt": (
            "A cota de solicitações ao assistente de IA foi atingida por agora.\n\n"
            "Tente novamente em {delay}."
        ),
        "zh": "AI 助手的请求配额目前已达上限。\n\n请在 {delay} 后重试。",
    },
    "msg.delay_seconds": {
        "fr": "{seconds} secondes",
        "en": "{seconds} seconds",
        "es": "{seconds} segundos",
        "pt": "{seconds} segundos",
        "zh": "{seconds} 秒",
    },
    "msg.delay_moment": {
        "fr": "quelques instants",
        "en": "a few moments",
        "es": "unos instantes",
        "pt": "alguns instantes",
        "zh": "片刻",
    },
    "msg.edit_title": {
        "fr": "Modification",
        "en": "Editing",
        "es": "Modificación",
        "pt": "Edição",
        "zh": "修改",
    },
    "msg.edit_doc_title": {
        "fr": "MIrAI – Édition du document",
        "en": "MIrAI – Editing the document",
        "es": "MIrAI – Edición del documento",
        "pt": "MIrAI – Edição do documento",
        "zh": "MIrAI – 编辑文档",
    },
    "msg.document_empty": {
        "fr": "Document vide.",
        "en": "Empty document.",
        "es": "Documento vacío.",
        "pt": "Documento vazio.",
        "zh": "文档为空。",
    },
    "msg.no_change": {
        "fr": "Aucune modification applicable trouvée dans le document.",
        "en": "No applicable change found in the document.",
        "es": "No se encontró ninguna modificación aplicable en el documento.",
        "pt": "Nenhuma alteração aplicável foi encontrada no documento.",
        "zh": "文档中未找到可应用的修改。",
    },
    "msg.ask_instead": {
        "fr": "Le modèle a tenté de poser une question. Reformulez la demande de manière plus directive.",
        "en": "The model tried to ask a question. Rephrase the request more directly.",
        "es": "El modelo intentó hacer una pregunta. Reformule la solicitud de forma más directiva.",
        "pt": "O modelo tentou fazer uma pergunta. Reformule a solicitação de forma mais direta.",
        "zh": "模型试图提问。请以更明确的方式重新表述请求。",
    },
    "msg.no_answer": {
        "fr": "Aucune réponse reçue du modèle. Vérifiez le token et réessayez.",
        "en": "No response received from the model. Check the token and try again.",
        "es": "No se recibió respuesta del modelo. Compruebe el token e inténtelo de nuevo.",
        "pt": "Nenhuma resposta recebida do modelo. Verifique o token e tente novamente.",
        "zh": "未收到模型响应。请检查令牌并重试。",
    },
    "msg.action_failed_title": {
        "fr": "Action impossible",
        "en": "Action failed",
        "es": "Acción imposible",
        "pt": "Ação impossível",
        "zh": "操作失败",
    },
    "msg.action_failed_body": {
        "fr": "« {action} » n'a pas pu s'exécuter.\n\n{exc}",
        "en": "\"{action}\" could not run.\n\n{exc}",
        "es": "«{action}» no se pudo ejecutar.\n\n{exc}",
        "pt": "«{action}» não pôde ser executada.\n\n{exc}",
        "zh": "“{action}” 无法执行。\n\n{exc}",
    },
    "msg.action_unavailable_title": {
        "fr": "Action indisponible",
        "en": "Action unavailable",
        "es": "Acción no disponible",
        "pt": "Ação indisponível",
        "zh": "操作不可用",
    },
    "msg.action_unavailable_body": {
        "fr": (
            "L'action « {action} » n'est pas disponible ici.\n\n"
            "Ouvrez un document Writer ou Calc, puis réessayez."
        ),
        "en": (
            "The \"{action}\" action is not available here.\n\n"
            "Open a Writer or Calc document, then try again."
        ),
        "es": (
            "La acción «{action}» no está disponible aquí.\n\n"
            "Abra un documento de Writer o Calc e inténtelo de nuevo."
        ),
        "pt": (
            "A ação «{action}» não está disponível aqui.\n\n"
            "Abra um documento do Writer ou Calc e tente novamente."
        ),
        "zh": "“{action}” 操作在此处不可用。\n\n请打开 Writer 或 Calc 文档后重试。",
    },
    # --- Directive de langue pour les réponses du LLM ---
    # Les prompts métier restent en français (décision de conception) ; cette
    # directive est injectée dans les requêtes dont la réponse s'adresse à
    # l'utilisateur (conversation, suggestions, explications), pour qu'elle
    # suive la langue choisie dans l'interface.
    "llm.answer_language": {
        "fr": (
            "RÈGLE DE LANGUE : tu DOIS répondre en français, sauf si la tâche "
            "demande explicitement une autre langue (par exemple une traduction)."
        ),
        "en": (
            "LANGUAGE RULE: you MUST answer in English, unless the task "
            "explicitly requires another language (for example a translation)."
        ),
        "es": (
            "REGLA DE IDIOMA: DEBES responder en español, salvo que la tarea "
            "exija explícitamente otro idioma (por ejemplo, una traducción)."
        ),
        "pt": (
            "REGRA DE IDIOMA: você DEVE responder em português, salvo se a tarefa "
            "exigir explicitamente outro idioma (por exemplo, uma tradução)."
        ),
        "zh": "语言规则：你必须用中文回答，除非任务明确要求使用其他语言（例如翻译）。",
    },
    # --- Palette de l'assistant : chips, runneurs, journal, statuts ---
    "preset.summarize": {
        "fr": "📝 Résumer",
        "en": "📝 Summarize",
        "es": "📝 Resumir",
        "pt": "📝 Resumir",
        "zh": "📝 总结",
    },
    "preset.simplify": {
        "fr": "💬 Simplifier",
        "en": "💬 Simplify",
        "es": "💬 Simplificar",
        "pt": "💬 Simplificar",
        "zh": "💬 简化",
    },
    "preset.shorten": {
        "fr": "📏− Raccourcir",
        "en": "📏− Shorten",
        "es": "📏− Acortar",
        "pt": "📏− Encurtar",
        "zh": "📏− 缩短",
    },
    "preset.lengthen": {
        "fr": "📏+ Allonger",
        "en": "📏+ Lengthen",
        "es": "📏+ Alargar",
        "pt": "📏+ Alongar",
        "zh": "📏+ 扩写",
    },
    "preset.transform": {
        "fr": "🔄 Transformer",
        "en": "🔄 Transform",
        "es": "🔄 Transformar",
        "pt": "🔄 Transformar",
        "zh": "🔄 转换",
    },
    "preset.formula": {
        "fr": "🧮 Formule",
        "en": "🧮 Formula",
        "es": "🧮 Fórmula",
        "pt": "🧮 Fórmula",
        "zh": "🧮 公式",
    },
    "preset.analyze": {
        "fr": "📊 Analyser",
        "en": "📊 Analyze",
        "es": "📊 Analizar",
        "pt": "📊 Analisar",
        "zh": "📊 分析",
    },
    "preset.transform_hint": {
        "fr": "Décrivez la transformation (ex. « traduire en anglais »)",
        "en": 'Describe the transformation (e.g. "translate into English")',
        "es": "Describa la transformación (p. ej. «traducir al inglés»)",
        "pt": "Descreva a transformação (ex. «traduzir para inglês»)",
        "zh": "描述转换操作（例如“翻译成英语”）",
    },
    "preset.formula_hint": {
        "fr": "Décrivez la formule (ex. « moyenne des ventes 2024 »)",
        "en": 'Describe the formula (e.g. "average of 2024 sales")',
        "es": "Describa la fórmula (p. ej. «promedio de ventas 2024»)",
        "pt": "Descreva a fórmula (ex. «média das vendas de 2024»)",
        "zh": "描述公式（例如“2024 年销售额平均值”）",
    },
    "preset.undo_extend": {
        "fr": "Générer la suite",
        "en": "Generate the continuation",
        "es": "Generar la continuación",
        "pt": "Gerar a continuação",
        "zh": "生成续写",
    },
    "preset.undo_summarize": {
        "fr": "Résumer",
        "en": "Summarize",
        "es": "Resumir",
        "pt": "Resumir",
        "zh": "总结",
    },
    "preset.undo_simplify": {
        "fr": "Reformuler",
        "en": "Rephrase",
        "es": "Reformular",
        "pt": "Reformular",
        "zh": "改写",
    },
    "preset.undo_shorten": {
        "fr": "Raccourcir",
        "en": "Shorten",
        "es": "Acortar",
        "pt": "Encurtar",
        "zh": "缩短",
    },
    "preset.undo_lengthen": {
        "fr": "Allonger",
        "en": "Lengthen",
        "es": "Alargar",
        "pt": "Alongar",
        "zh": "扩写",
    },
    "preset.undo_transform": {
        "fr": "Transformer en colonne",
        "en": "Transform into a column",
        "es": "Transformar en columna",
        "pt": "Transformar em coluna",
        "zh": "转换为列",
    },
    "preset.undo_analyze": {
        "fr": "Analyser la plage",
        "en": "Analyze the range",
        "es": "Analizar el rango",
        "pt": "Analisar o intervalo",
        "zh": "分析区域",
    },
    "preset.undo_edit_selection": {
        "fr": "Modifier la sélection",
        "en": "Edit the selection",
        "es": "Modificar la selección",
        "pt": "Modificar a seleção",
        "zh": "修改选区",
    },
    "preset.need_range": {
        "fr": "Sélectionnez d'abord la plage de données à analyser.",
        "en": "First select the range of data to analyze.",
        "es": "Seleccione primero el rango de datos que desea analizar.",
        "pt": "Selecione primeiro o intervalo de dados a analisar.",
        "zh": "请先选择要分析的数据区域。",
    },
    "preset.need_transform_input": {
        "fr": "Tapez d'abord l'instruction de transformation dans le prompt.",
        "en": "First type the transformation instruction in the prompt.",
        "es": "Escriba primero la instrucción de transformación en el campo.",
        "pt": "Digite primeiro a instrução de transformação no campo.",
        "zh": "请先在输入框中输入转换指令。",
    },
    "preset.resized": {
        "fr": "Texte ajusté (~{target} mots). Recliquez pour itérer.",
        "en": "Text adjusted (~{target} words). Click again to iterate.",
        "es": "Texto ajustado (~{target} palabras). Vuelva a pulsar para iterar.",
        "pt": "Texto ajustado (~{target} palavras). Clique novamente para iterar.",
        "zh": "文本已调整（约 {target} 词）。可再次点击迭代。",
    },
    "preset.done_extend": {
        "fr": "Suite générée dans le document.",
        "en": "Continuation generated in the document.",
        "es": "Continuación generada en el documento.",
        "pt": "Continuação gerada no documento.",
        "zh": "续写已生成到文档中。",
    },
    "preset.done_summarize": {
        "fr": "Résumé inséré après la sélection.",
        "en": "Summary inserted after the selection.",
        "es": "Resumen insertado tras la selección.",
        "pt": "Resumo inserido após a seleção.",
        "zh": "摘要已插入到选区之后。",
    },
    "preset.done_simplify": {
        "fr": "Reformulation insérée après la sélection.",
        "en": "Rephrasing inserted after the selection.",
        "es": "Reformulación insertada tras la selección.",
        "pt": "Reformulação inserida após a seleção.",
        "zh": "改写内容已插入到选区之后。",
    },
    "preset.done_transform": {
        "fr": "{count} ligne(s) transformée(s) en colonne {col}.",
        "en": "{count} row(s) transformed into column {col}.",
        "es": "{count} fila(s) transformada(s) en la columna {col}.",
        "pt": "{count} linha(s) transformada(s) na coluna {col}.",
        "zh": "已将 {count} 行转换为 {col} 列。",
    },
    "preset.done_analyze": {
        "fr": "Analyse écrite sous la sélection.",
        "en": "Analysis written below the selection.",
        "es": "Análisis escrito bajo la selección.",
        "pt": "Análise escrita abaixo da seleção.",
        "zh": "分析结果已写入选区下方。",
    },
    "sel.writer_selection": {
        "fr": "Sélection : « {excerpt} »",
        "en": 'Selection: "{excerpt}"',
        "es": "Selección: «{excerpt}»",
        "pt": "Seleção: “{excerpt}”",
        "zh": "选区：“{excerpt}”",
    },
    "sel.no_selection": {
        "fr": "Document entier — les actions rapides portent sur le paragraphe courant",
        "en": "Whole document — quick actions apply to the current paragraph",
        "es": "Documento completo — las acciones rápidas se aplican al párrafo actual",
        "pt": "Documento inteiro — as ações rápidas atuam no parágrafo atual",
        "zh": "整个文档 — 快速操作作用于当前段落",
    },
    "sel.no_target": {
        "fr": "Placez le curseur dans un paragraphe ou sélectionnez du texte.",
        "en": "Place the cursor in a paragraph or select some text.",
        "es": "Coloque el cursor en un párrafo o seleccione texto.",
        "pt": "Coloque o cursor em um parágrafo ou selecione um texto.",
        "zh": "请将光标置于段落中或选择文本。",
    },
    "sel.calc_cell": {
        "fr": "Cellule sélectionnée ({ref})",
        "en": "Selected cell ({ref})",
        "es": "Celda seleccionada ({ref})",
        "pt": "Célula selecionada ({ref})",
        "zh": "已选单元格（{ref}）",
    },
    "sel.calc_range": {
        "fr": "{count} cellules sélectionnées ({first}:{last})",
        "en": "{count} cells selected ({first}:{last})",
        "es": "{count} celdas seleccionadas ({first}:{last})",
        "pt": "{count} células selecionadas ({first}:{last})",
        "zh": "已选择 {count} 个单元格（{first}:{last}）",
    },
    "suggestion.w_summarize": {
        "fr": "Résumer ce passage",
        "en": "Summarize this passage",
        "es": "Resumir este pasaje",
        "pt": "Resumir esta passagem",
        "zh": "总结这段文字",
    },
    "suggestion.w_shorten": {
        "fr": "Le rendre plus court",
        "en": "Make it shorter",
        "es": "Hacerlo más corto",
        "pt": "Torná-lo mais curto",
        "zh": "把它缩短",
    },
    "suggestion.w_simplify_passage": {
        "fr": "Simplifier la formulation",
        "en": "Simplify the wording",
        "es": "Simplificar la formulación",
        "pt": "Simplificar a formulação",
        "zh": "简化表达",
    },
    "suggestion.w_lengthen": {
        "fr": "Développer ce passage",
        "en": "Expand this passage",
        "es": "Desarrollar este pasaje",
        "pt": "Desenvolver esta passagem",
        "zh": "扩写这段文字",
    },
    "suggestion.w_lengthen_phrase": {
        "fr": "Développer cette phrase",
        "en": "Expand this sentence",
        "es": "Desarrollar esta frase",
        "pt": "Desenvolver esta frase",
        "zh": "扩写这句话",
    },
    "suggestion.w_simplify_paragraph": {
        "fr": "Simplifier ce paragraphe",
        "en": "Simplify this paragraph",
        "es": "Simplificar este párrafo",
        "pt": "Simplificar este parágrafo",
        "zh": "简化这段",
    },
    "suggestion.w_spellcheck": {
        "fr": "Corrige l'orthographe et la grammaire",
        "en": "Fix spelling and grammar",
        "es": "Corrige la ortografía y la gramática",
        "pt": "Corrija a ortografia e a gramática",
        "zh": "纠正拼写和语法",
    },
    "suggestion.w_formal": {
        "fr": "Rends le ton plus formel",
        "en": "Make the tone more formal",
        "es": "Haz el tono más formal",
        "pt": "Torne o tom mais formal",
        "zh": "使语气更正式",
    },
    "suggestion.w_translate_en": {
        "fr": "Traduis en anglais",
        "en": "Translate into English",
        "es": "Traduce al inglés",
        "pt": "Traduza para inglês",
        "zh": "翻译成英语",
    },
    "suggestion.c_formula": {
        "fr": "Écrire une formule",
        "en": "Write a formula",
        "es": "Escribir una fórmula",
        "pt": "Escrever uma fórmula",
        "zh": "编写公式",
    },
    "suggestion.c_analyze": {
        "fr": "Analyser ces chiffres",
        "en": "Analyze these figures",
        "es": "Analizar estas cifras",
        "pt": "Analisar estes números",
        "zh": "分析这些数字",
    },
    "suggestion.c_stats": {
        "fr": "Calculer la moyenne et le total",
        "en": "Compute the average and the total",
        "es": "Calcular la media y el total",
        "pt": "Calcular a média e o total",
        "zh": "计算平均值和总和",
    },
    "suggestion.c_upper": {
        "fr": "Mettre en majuscules",
        "en": "Convert to uppercase",
        "es": "Poner en mayúsculas",
        "pt": "Converter em maiúsculas",
        "zh": "转换为大写",
    },
    "suggestion.c_sort": {
        "fr": "Classer par catégorie",
        "en": "Sort by category",
        "es": "Clasificar por categoría",
        "pt": "Classificar por categoria",
        "zh": "按类别排序",
    },
    "suggestion.c_outliers": {
        "fr": "Repérer les anomalies",
        "en": "Spot the outliers",
        "es": "Detectar las anomalías",
        "pt": "Detectar as anomalias",
        "zh": "找出异常值",
    },
    "suggestion.none": {
        "fr": "Aucune suggestion pour cette sélection.",
        "en": "No suggestion for this selection.",
        "es": "Ninguna sugerencia para esta selección.",
        "pt": "Nenhuma sugestão para esta seleção.",
        "zh": "此选区暂无建议。",
    },
    "tab.conversation": {
        "fr": "Conversation",
        "en": "Conversation",
        "es": "Conversación",
        "pt": "Conversa",
        "zh": "对话",
    },
    "tab.suggestions": {
        "fr": "Suggestions",
        "en": "Suggestions",
        "es": "Sugerencias",
        "pt": "Sugestões",
        "zh": "建议",
    },
    "tab.reasoning": {
        "fr": "Raisonnement",
        "en": "Reasoning",
        "es": "Razonamiento",
        "pt": "Raciocínio",
        "zh": "推理",
    },
    "tab.actions": {
        "fr": "Actions",
        "en": "Actions",
        "es": "Acciones",
        "pt": "Ações",
        "zh": "操作",
    },
    "tool.writer_get_selection": {
        "fr": "Lecture de la sélection",
        "en": "Reading the selection",
        "es": "Lectura de la selección",
        "pt": "Leitura da seleção",
        "zh": "读取选区",
    },
    "tool.calc_get_selection": {
        "fr": "Lecture de la sélection",
        "en": "Reading the selection",
        "es": "Lectura de la selección",
        "pt": "Leitura da seleção",
        "zh": "读取选区",
    },
    "tool.writer_get_document_map": {
        "fr": "Lecture du document",
        "en": "Reading the document",
        "es": "Lectura del documento",
        "pt": "Leitura do documento",
        "zh": "读取文档",
    },
    "tool.writer_replace_paragraphs": {
        "fr": "Réécriture des paragraphes",
        "en": "Rewriting the paragraphs",
        "es": "Reescritura de los párrafos",
        "pt": "Reescrita dos parágrafos",
        "zh": "重写段落",
    },
    "tool.writer_replace_selection": {
        "fr": "Remplacement de la sélection",
        "en": "Replacing the selection",
        "es": "Reemplazo de la selección",
        "pt": "Substituição da seleção",
        "zh": "替换选区",
    },
    "tool.writer_insert_text": {
        "fr": "Insertion de texte",
        "en": "Inserting text",
        "es": "Inserción de texto",
        "pt": "Inserção de texto",
        "zh": "插入文本",
    },
    "tool.writer_find_replace": {
        "fr": "Remplacements dans le document",
        "en": "Find-and-replace in the document",
        "es": "Reemplazos en el documento",
        "pt": "Substituições no documento",
        "zh": "文档内查找替换",
    },
    "tool.calc_read_range": {
        "fr": "Lecture d'une plage",
        "en": "Reading a range",
        "es": "Lectura de un rango",
        "pt": "Leitura de um intervalo",
        "zh": "读取区域",
    },
    "tool.calc_get_sheet_overview": {
        "fr": "Analyse de la structure",
        "en": "Structure analysis",
        "es": "Análisis de la estructura",
        "pt": "Análise da estrutura",
        "zh": "结构分析",
    },
    "tool.calc_write_cells": {
        "fr": "Écriture de cellules",
        "en": "Writing cells",
        "es": "Escritura de celdas",
        "pt": "Escrita de células",
        "zh": "写入单元格",
    },
    "tool.calc_write_result_column": {
        "fr": "Écriture de la colonne résultat",
        "en": "Writing the result column",
        "es": "Escritura de la columna de resultados",
        "pt": "Escrita da coluna de resultado",
        "zh": "写入结果列",
    },
    "tool.calc_set_formula": {
        "fr": "Écriture de la formule",
        "en": "Writing the formula",
        "es": "Escritura de la fórmula",
        "pt": "Escrita da fórmula",
        "zh": "写入公式",
    },
    "tool.calc_fill_formula_down": {
        "fr": "Recopie de la formule",
        "en": "Filling the formula down",
        "es": "Relleno de la fórmula",
        "pt": "Preenchimento da fórmula",
        "zh": "向下填充公式",
    },
    "reasoning.empty": {
        "fr": "Le raisonnement du modèle s'affichera ici pendant la prochaine demande.",
        "en": "The model's reasoning will appear here during the next request.",
        "es": "El razonamiento del modelo aparecerá aquí durante la próxima solicitud.",
        "pt": "O raciocínio do modelo aparecerá aqui durante a próxima solicitação.",
        "zh": "下次请求期间，模型的推理将显示在这里。",
    },
    "palette.title": {
        "fr": "MIrAI — Assistant",
        "en": "MIrAI — Assistant",
        "es": "MIrAI — Asistente",
        "pt": "MIrAI — Assistente",
        "zh": "MIrAI — 助手",
    },
    "palette.header": {
        "fr": "  MIrAI — Assistant ({app})",
        "en": "  MIrAI — Assistant ({app})",
        "es": "  MIrAI — Asistente ({app})",
        "pt": "  MIrAI — Assistente ({app})",
        "zh": "  MIrAI — 助手（{app}）",
    },
    "palette.prompt_help": {
        "fr": "Décrivez ce que l'assistant doit faire",
        "en": "Describe what the assistant should do",
        "es": "Describa lo que debe hacer el asistente",
        "pt": "Descreva o que o assistente deve fazer",
        "zh": "描述您希望助手做什么",
    },
    "palette.reasoning_help": {
        "fr": "Voir ce que le modèle est en train de faire",
        "en": "See what the model is doing",
        "es": "Ver qué está haciendo el modelo",
        "pt": "Ver o que o modelo está fazendo",
        "zh": "查看模型正在做什么",
    },
    "palette.append_label": {
        "fr": "Ajouter à la suite",
        "en": "Add after the selection",
        "es": "Añadir a continuación",
        "pt": "Adicionar a seguir",
        "zh": "追加到后方",
    },
    "palette.append_help": {
        "fr": (
            "Coché : le résultat est inséré après la sélection, entre "
            "marqueurs, et l'original est conservé.\n"
            "Décoché : le résultat remplace la sélection."
        ),
        "en": (
            "Checked: the result is inserted after the selection, "
            "between markers, and the original is kept.\n"
            "Unchecked: the result replaces the selection."
        ),
        "es": (
            "Marcada: el resultado se inserta tras la selección, entre "
            "marcadores, y el original se conserva.\n"
            "Desmarcada: el resultado reemplaza la selección."
        ),
        "pt": (
            "Marcada: o resultado é inserido após a seleção, entre "
            "marcadores, e o original é conservado.\n"
            "Desmarcada: o resultado substitui a seleção."
        ),
        "zh": (
            "勾选：结果会插入到选区之后，位于标记之间，并保留原文。\n"
            "不勾选：结果将替换选区。"
        ),
    },
    "palette.send": {
        "fr": "Envoyer  ⏎",
        "en": "Send  ⏎",
        "es": "Enviar  ⏎",
        "pt": "Enviar  ⏎",
        "zh": "发送  ⏎",
    },
    "palette.stop": {
        "fr": "Arrêter",
        "en": "Stop",
        "es": "Detener",
        "pt": "Parar",
        "zh": "停止",
    },
    "palette.new_conversation": {
        "fr": "🗑 Nouvelle conversation",
        "en": "🗑 New conversation",
        "es": "🗑 Nueva conversación",
        "pt": "🗑 Nova conversa",
        "zh": "🗑 新对话",
    },
    "palette.hint": {
        "fr": "Entrée : envoyer · Échap : fermer",
        "en": "Enter: send · Esc: close",
        "es": "Intro: enviar · Esc: cerrar",
        "pt": "Enter: enviar · Esc: fechar",
        "zh": "Enter：发送 · Esc：关闭",
    },
    "palette.ready": {
        "fr": "Prêt",
        "en": "Ready",
        "es": "Listo",
        "pt": "Pronto",
        "zh": "就绪",
    },
    "palette.done": {
        "fr": "Terminé",
        "en": "Done",
        "es": "Hecho",
        "pt": "Concluído",
        "zh": "完成",
    },
    "palette.cleared": {
        "fr": "Conversation effacée.",
        "en": "Conversation cleared.",
        "es": "Conversación borrada.",
        "pt": "Conversa apagada.",
        "zh": "对话已清空。",
    },
    "palette.stopping": {
        "fr": "Arrêt en cours…",
        "en": "Stopping…",
        "es": "Deteniendo…",
        "pt": "Parando…",
        "zh": "正在停止…",
    },
    "palette.working": {
        "fr": "L'assistant travaille…",
        "en": "The assistant is working…",
        "es": "El asistente está trabajando…",
        "pt": "O assistente está trabalhando…",
        "zh": "助手正在处理…",
    },
    "palette.refuse_empty": {
        "fr": "Tapez d'abord votre demande.",
        "en": "Type your request first.",
        "es": "Escriba primero su solicitud.",
        "pt": "Digite primeiro sua solicitação.",
        "zh": "请先输入您的请求。",
    },
    "palette.refuse_input_hint": {
        "fr": "Précisez votre demande.",
        "en": "Specify your request.",
        "es": "Concreta su solicitud.",
        "pt": "Especifique sua solicitação.",
        "zh": "请明确您的请求。",
    },
    "palette.refuse_document": {
        "fr": "Ouvrez un document Writer ou Calc.",
        "en": "Open a Writer or Calc document.",
        "es": "Abra un documento de Writer o Calc.",
        "pt": "Abra um documento do Writer ou Calc.",
        "zh": "请打开 Writer 或 Calc 文档。",
    },
    "palette.refuse_app": {
        "fr": "Cette action nécessite un document {app}.",
        "en": "This action requires a {app} document.",
        "es": "Esta acción requiere un documento de {app}.",
        "pt": "Esta ação exige um documento do {app}.",
        "zh": "此操作需要 {app} 文档。",
    },
    "palette.user_prefix": {
        "fr": "Vous : ",
        "en": "You: ",
        "es": "Tú: ",
        "pt": "Você: ",
        "zh": "用户：",
    },
    "palette.assistant_prefix": {
        "fr": "MIrAI : ",
        "en": "MIrAI: ",
        "es": "MIrAI: ",
        "pt": "MIrAI: ",
        "zh": "MIrAI：",
    },
    "palette.applied": {
        "fr": "Modification appliquée.",
        "en": "Change applied.",
        "es": "Cambio aplicado.",
        "pt": "Alteração aplicada.",
        "zh": "修改已应用。",
    },
    "palette.empty_document": {
        "fr": "Le document est vide.",
        "en": "The document is empty.",
        "es": "El documento está vacío.",
        "pt": "O documento está vazio.",
        "zh": "文档为空。",
    },
    "palette.headings_only": {
        "fr": "Ce document ne contient que des titres.",
        "en": "This document contains only headings.",
        "es": "Este documento solo contiene títulos.",
        "pt": "Este documento contém apenas títulos.",
        "zh": "此文档只包含标题。",
    },
    "palette.mode_tools": {
        "fr": "Mode outils : {mode}",
        "en": "Tool mode: {mode}",
        "es": "Modo de herramientas: {mode}",
        "pt": "Modo de ferramentas: {mode}",
        "zh": "工具模式：{mode}",
    },
    "palette.no_action": {
        "fr": "ℹ Aucune action sur le document — réponse en texte seul.",
        "en": "ℹ No action on the document — plain text answer only.",
        "es": "ℹ Ninguna acción sobre el documento — respuesta solo en texto.",
        "pt": "ℹ Nenhuma ação sobre o documento — resposta apenas em texto.",
        "zh": "ℹ 未对文档执行操作 — 仅返回纯文本。",
    },
    "palette.capabilities": {
        "fr": "   Ici, l'assistant sait : {tools}.",
        "en": "   Here, the assistant can: {tools}.",
        "es": "   Aquí, el asistente sabe: {tools}.",
        "pt": "   Aqui, o assistente sabe: {tools}.",
        "zh": "   此处助手可以：{tools}。",
    },
    "palette.journal_prepare": {
        "fr": "⚙ {label} — préparation",
        "en": "⚙ {label} — preparing",
        "es": "⚙ {label} — preparación",
        "pt": "⚙ {label} — preparação",
        "zh": "⚙ {label} — 准备",
    },
    "palette.journal_selection_start": {
        "fr": "⚙ Modification de la sélection ({chars} car.)",
        "en": "⚙ Editing the selection ({chars} chars)",
        "es": "⚙ Modificación de la selección ({chars} car.)",
        "pt": "⚙ Modificação da seleção ({chars} car.)",
        "zh": "⚙ 修改选区（{chars} 字符）",
    },
    "palette.journal_selection_done": {
        "fr": "✓ Sélection {how}",
        "en": "✓ Selection {how}",
        "es": "✓ Selección {how}",
        "pt": "✓ Seleção {how}",
        "zh": "✓ 选区{how}",
    },
    "palette.selection_summary": {
        "fr": "Sélection {how}. Ctrl+Z pour annuler.",
        "en": "Selection {how}. Ctrl+Z to undo.",
        "es": "Selección {how}. Ctrl+Z para deshacer.",
        "pt": "Seleção {how}. Ctrl+Z para desfazer.",
        "zh": "选区{how}。Ctrl+Z 可撤销。",
    },
    "palette.how_appended_selection": {
        "fr": "ajouté après la sélection",
        "en": "added after the selection",
        "es": "añadido tras la selección",
        "pt": "adicionado após a seleção",
        "zh": "已追加至选区后",
    },
    "palette.how_replaced": {
        "fr": "remplacée",
        "en": "replaced",
        "es": "reemplazada",
        "pt": "substituída",
        "zh": "已被替换",
    },
    "palette.journal_read": {
        "fr": "✓ Lecture du document — {count} paragraphe(s)",
        "en": "✓ Document read — {count} paragraph(s)",
        "es": "✓ Lectura do documento — {count} párrafo(s)",
        "pt": "✓ Leitura do documento — {count} parágrafo(s)",
        "zh": "✓ 已读取文档 — {count} 段",
    },
    "palette.journal_heading_kept": {
        "fr": "↳ Titre conservé",
        "en": "↳ Heading kept",
        "es": "↳ Título conservado",
        "pt": "↳ Título conservado",
        "zh": "↳ 保留标题",
    },
    "palette.journal_rewrite_start": {
        "fr": "⚙ Réécriture des paragraphes {first} à {last}",
        "en": "⚙ Rewriting paragraphs {first} to {last}",
        "es": "⚙ Reescritura de los párrafos {first} a {last}",
        "pt": "⚙ Reescrita dos parágrafos {first} a {last}",
        "zh": "⚙ 重写第 {first} 至 {last} 段",
    },
    "palette.journal_written": {
        "fr": "✓ Écriture appliquée — {before} → {after} paragraphe(s)",
        "en": "✓ Write applied — {before} → {after} paragraph(s)",
        "es": "✓ Escritura aplicada — {before} → {after} párrafo(s)",
        "pt": "✓ Escrita aplicada — {before} → {after} parágrafo(s)",
        "zh": "✓ 已写入 — {before} → {after} 段",
    },
    "palette.title_kept": {
        "fr": " (titre conservé)",
        "en": " (heading kept)",
        "es": " (título conservado)",
        "pt": " (título conservado)",
        "zh": "（保留标题）",
    },
    "palette.how_appended": {
        "fr": "ajouté à la suite",
        "en": "appended",
        "es": "añadido a continuación",
        "pt": "adicionado a seguir",
        "zh": "已追加",
    },
    "palette.how_rewritten": {
        "fr": "réécrit",
        "en": "rewritten",
        "es": "reescrito",
        "pt": "reescrito",
        "zh": "已重写",
    },
    "palette.document_summary": {
        "fr": "Document {how} : {before} → {after} paragraphe(s){kept}. Ctrl+Z pour annuler.",
        "en": "Document {how}: {before} → {after} paragraph(s){kept}. Ctrl+Z to undo.",
        "es": "Documento {how}: {before} → {after} párrafo(s){kept}. Ctrl+Z para deshacer.",
        "pt": "Documento {how}: {before} → {after} parágrafo(s){kept}. Ctrl+Z para desfazer.",
        "zh": "文档{how}：{before} → {after} 段{kept}。Ctrl+Z 可撤销。",
    },
    "palette.budget_starved": {
        "fr": (
            "\n⚠ Ce modèle a consacré tout son budget à réfléchir sans "
            "produire de réponse. Le document n'a pas été modifié. "
            "Essayez un modèle qui raisonne moins (Paramètres), ou une "
            "demande portant sur une partie du document."
        ),
        "en": (
            "\n⚠ This model spent its entire budget reasoning without "
            "producing an answer. The document was not modified. Try a "
            "model that reasons less (Settings), or a request targeting "
            "part of the document."
        ),
        "es": (
            "\n⚠ Este modelo dedicó todo su presupuesto a razonar sin "
            "producir una respuesta. El documento no se modificó. "
            "Pruebe con un modelo que razone menos (Ajustes), o una "
            "solicitud sobre una parte del documento."
        ),
        "pt": (
            "\n⚠ Este modelo gastou todo o orçamento raciocinando sem "
            "produzir uma resposta. O documento não foi modificado. "
            "Experimente um modelo que raciocine menos (Configurações), "
            "ou uma solicitação sobre parte do documento."
        ),
        "zh": (
            "\n⚠ 该模型将全部额度用于推理，未产生任何回答。文档未被修改。"
            "请尝试推理较少的模型（设置），或针对文档局部的要求。"
        ),
    },
    "palette.unusable": {
        "fr": "\n⚠ Réponse inexploitable — le document n'a pas été modifié.",
        "en": "\n⚠ Unusable reply — the document was not modified.",
        "es": "\n⚠ Respuesta inutilizable — el documento no se modificó.",
        "pt": "\n⚠ Resposta inutilizável — o documento não foi modificado.",
        "zh": "\n⚠ 回答无法使用 — 文档未被修改。",
    },
    "palette.journal_budget_starved": {
        "fr": "✗ Budget épuisé par le raisonnement",
        "en": "✗ Budget exhausted by reasoning",
        "es": "✗ Presupuesto agotado por el razonamiento",
        "pt": "✗ Orçamento esgotado pelo raciocínio",
        "zh": "✗ 额度被推理耗尽",
    },
    "palette.journal_unusable": {
        "fr": "✗ Réponse inexploitable",
        "en": "✗ Unusable reply",
        "es": "✗ Respuesta inutilizable",
        "pt": "✗ Resposta inutilizável",
        "zh": "✗ 回答无法使用",
    },
    "palette.err_token": {
        "fr": "Jeton expiré — Menu MIrAI ▸ Paramètres pour vous reconnecter.",
        "en": "Token expired — MIrAI menu ▸ Settings to sign in again.",
        "es": "Token caducado — Menú MIrAI ▸ Ajustes para reconectarse.",
        "pt": "Token expirado — Menu MIrAI ▸ Configurações para reconectar.",
        "zh": "令牌已过期 — 请通过 MIrAI 菜单 ▸ 设置重新登录。",
    },
    "palette.err_timeout": {
        "fr": "Le service n'a pas répondu à temps. Réessayez dans un instant.",
        "en": "The service did not respond in time. Try again shortly.",
        "es": "El servicio no respondió a tiempo. Reintente en un momento.",
        "pt": "O serviço não respondeu a tempo. Tente novamente em instantes.",
        "zh": "服务未能及时响应。请稍后重试。",
    },
    "palette.err_busy": {
        "fr": "LibreOffice était occupé (fenêtre ouverte ?). Réessayez.",
        "en": "LibreOffice was busy (open dialog?). Try again.",
        "es": "LibreOffice estaba ocupado (¿ventana abierta?). Reintente.",
        "pt": "O LibreOffice estava ocupado (janela aberta?). Tente novamente.",
        "zh": "LibreOffice 正忙（对话框已打开？）。请重试。",
    },
    "palette.err_generic": {
        "fr": "Erreur : {text}",
        "en": "Error: {text}",
        "es": "Error: {text}",
        "pt": "Erro: {text}",
        "zh": "错误：{text}",
    },
    "progress.connecting": {
        "fr": "Connexion…",
        "en": "Connecting…",
        "es": "Conexión…",
        "pt": "Conectando…",
        "zh": "连接中…",
    },
    "progress.writing": {
        "fr": "Rédaction",
        "en": "Writing",
        "es": "Redacción",
        "pt": "Redação",
        "zh": "撰写",
    },
    "progress.thinking": {
        "fr": "Réflexion",
        "en": "Thinking",
        "es": "Reflexión",
        "pt": "Reflexão",
        "zh": "思考",
    },
    "progress.action": {
        "fr": "Action sur le document",
        "en": "Acting on the document",
        "es": "Acción sobre el documento",
        "pt": "Ação sobre o documento",
        "zh": "正在操作文档",
    },
    "progress.applying": {
        "fr": "Application au document",
        "en": "Applying to the document",
        "es": "Aplicación al documento",
        "pt": "Aplicação ao documento",
        "zh": "正在应用到文档",
    },
    "progress.tooltip_reasoning": {
        "fr": "Réflexion du modèle :\n\n",
        "en": "Model reasoning:\n\n",
        "es": "Razonamiento del modelo:\n\n",
        "pt": "Raciocínio do modelo:\n\n",
        "zh": "模型推理：\n\n",
    },
    "progress.tooltip_text": {
        "fr": "Texte en cours :\n\n",
        "en": "Current text:\n\n",
        "es": "Texto en curso:\n\n",
        "pt": "Texto em andamento:\n\n",
        "zh": "当前文本：\n\n",
    },
    "run.err_401": {
        "fr": (
            "Votre poste n'est pas authentifié auprès du service IA. "
            "Ouvrez les Réglages pour vous reconnecter."
        ),
        "en": (
            "Your workstation is not authenticated with the AI service. "
            "Open Settings to sign in again."
        ),
        "es": (
            "Su equipo no está autenticado ante el servicio de IA. "
            "Abra Ajustes para reconectarse."
        ),
        "pt": (
            "Sua estação não está autenticada no serviço de IA. "
            "Abra as Configurações para reconectar."
        ),
        "zh": "您的设备尚未通过 AI 服务认证。请打开设置重新登录。",
    },
    "run.err_403": {
        "fr": (
            "Accès refusé par le relais — la configuration se "
            "resynchronise. Réessayez dans quelques instants."
        ),
        "en": (
            "Access denied by the relay — configuration is resynchronizing. "
            "Try again shortly."
        ),
        "es": (
            "Acceso denegado por el relé — la configuración se está "
            "resincronizando. Reintente en unos momentos."
        ),
        "pt": (
            "Acesso negado pelo relay — a configuração está sendo "
            "ressincronizada. Tente novamente em instantes."
        ),
        "zh": "中继拒绝访问 — 配置正在重新同步。请稍后重试。",
    },
    "run.err_429": {
        "fr": "Quota de requêtes atteint. Merci de réessayer dans quelques instants.",
        "en": "Request quota reached. Please try again shortly.",
        "es": "Cuota de solicitudes alcanzada. Inténtelo de nuevo en unos momentos.",
        "pt": "Cota de solicitações atingida. Tente novamente em instantes.",
        "zh": "请求配额已达上限。请稍后重试。",
    },
    "run.err_network": {
        "fr": "Le serveur IA est injoignable. Vérifiez votre connexion réseau puis réessayez.",
        "en": "The AI server is unreachable. Check your network connection and try again.",
        "es": "El servidor de IA no es accesible. Compruebe su conexión de red y reintente.",
        "pt": "O servidor de IA está inacessível. Verifique sua conexão de rede e tente novamente.",
        "zh": "无法连接 AI 服务器。请检查网络连接后重试。",
    },
    "run.err_empty": {
        "fr": "L'assistant n'a rien produit. Réessayez — si cela persiste, signalez-le.",
        "en": "The assistant produced nothing. Try again — if it persists, report it.",
        "es": "El asistente no produjo nada. Reintente — si persiste, repórtelo.",
        "pt": "O assistente não produziu nada. Tente novamente — se persistir, informe.",
        "zh": "助手没有生成任何内容。请重试；若持续出现，请报告。",
    },
    "run.err_generic": {
        "fr": "Erreur du service IA ({code}). Réessayez.",
        "en": "AI service error ({code}). Try again.",
        "es": "Error del servicio de IA ({code}). Reintente.",
        "pt": "Erro do serviço de IA ({code}). Tente novamente.",
        "zh": "AI 服务错误（{code}）。请重试。",
    },
    "run.stopped": {
        "fr": "Arrêté.",
        "en": "Stopped.",
        "es": "Detenido.",
        "pt": "Interrompido.",
        "zh": "已停止。",
    },
    "run.not_converged": {
        "fr": "L'assistant n'a pas convergé — réessayez en précisant la demande.",
        "en": "The assistant did not converge — try again with a more specific request.",
        "es": "El asistente no convergió — reintente precisando la solicitud.",
        "pt": "O assistente não convergiu — tente novamente especificando a solicitação.",
        "zh": "助手未能收敛 — 请更明确地描述请求后重试。",
    },
    "analysis.phase": {
        "fr": "Analyse du document",
        "en": "Document analysis",
        "es": "Análisis del documento",
        "pt": "Análise do documento",
        "zh": "文档分析",
    },
    "analysis.too_short": {
        "fr": (
            "Document trop court pour une analyse de structure.\n"
            "Écrivez quelques paragraphes, puis rouvrez cet onglet."
        ),
        "en": (
            "Document too short for a structural analysis.\n"
            "Write a few paragraphs, then reopen this tab."
        ),
        "es": (
            "Documento demasiado corto para un análisis de estructura.\n"
            "Escriba algunos párrafos y vuelva a abrir esta pestaña."
        ),
        "pt": (
            "Documento curto demais para uma análise de estrutura.\n"
            "Escreva alguns parágrafos e reabra esta aba."
        ),
        "zh": "文档太短，无法进行结构分析。\n请写几段文字后重新打开此标签页。",
    },
    "analysis.unavailable": {
        "fr": "Analyse indisponible pour le moment.",
        "en": "Analysis unavailable at the moment.",
        "es": "Análisis no disponible por el momento.",
        "pt": "Análise indisponível no momento.",
        "zh": "分析暂不可用。",
    },
    "analysis.header": {
        "fr": "Propositions d'amélioration du document :",
        "en": "Document improvement suggestions:",
        "es": "Propuestas de mejora del documento:",
        "pt": "Sugestões de melhoria do documento:",
        "zh": "文档改进建议：",
    },
    "entry.test_title": {
        "fr": "Test du modèle",
        "en": "Model test",
        "es": "Prueba del modelo",
        "pt": "Teste do modelo",
        "zh": "模型测试",
    },
    "entry.test_failed": {
        "fr": "Le test n'a pas pu aboutir.\n\n{detail}",
        "en": "The test could not complete.\n\n{detail}",
        "es": "La prueba no pudo completarse.\n\n{detail}",
        "pt": "O teste não pôde ser concluído.\n\n{detail}",
        "zh": "测试未能完成。\n\n{detail}",
    },
    "entry.model_line": {
        "fr": "Modèle : {model}",
        "en": "Model: {model}",
        "es": "Modelo: {model}",
        "pt": "Modelo: {model}",
        "zh": "模型：{model}",
    },
    "entry.model_undefined": {
        "fr": "(non défini)",
        "en": "(not set)",
        "es": "(sin definir)",
        "pt": "(não definido)",
        "zh": "（未设置）",
    },
    "entry.detail": {
        "fr": "Détail technique : {detail}",
        "en": "Technical detail: {detail}",
        "es": "Detalle técnico: {detail}",
        "pt": "Detalhe técnico: {detail}",
        "zh": "技术细节：{detail}",
    },
    "entry.need_document": {
        "fr": "Ouvrez un document Writer ou Calc pour utiliser l'assistant.",
        "en": "Open a Writer or Calc document to use the assistant.",
        "es": "Abra un documento de Writer o Calc para usar el asistente.",
        "pt": "Abra um documento do Writer ou Calc para usar o assistente.",
        "zh": "请打开 Writer 或 Calc 文档以使用助手。",
    },
    "caps.no_tools": {
        "fr": (
            "Ce modèle n'accepte pas les outils. Les modifications "
            "passeront par un chemin direct, piloté par l'extension."
        ),
        "en": (
            "This model does not accept tools. Changes will go through a "
            "direct path driven by the extension."
        ),
        "es": (
            "Este modelo no acepta herramientas. Las modificaciones pasarán "
            "por una ruta directa controlada por la extensión."
        ),
        "pt": (
            "Este modelo não aceita ferramentas. As modificações passarão "
            "por um caminho direto, controlado pela extensão."
        ),
        "zh": "该模型不接受工具。修改将通过由扩展掌控的直接路径完成。",
    },
    "caps.no_calls": {
        "fr": (
            "Ce modèle n'utilise pas les outils qu'on lui propose. "
            "Les modifications passeront par un chemin direct."
        ),
        "en": (
            "This model does not use the tools offered to it. Changes will "
            "go through a direct path."
        ),
        "es": (
            "Este modelo no usa las herramientas que se le proponen. Las "
            "modificaciones pasarán por una ruta directa."
        ),
        "pt": (
            "Este modelo não usa as ferramentas que lhe são oferecidas. As "
            "modificações passarão por um caminho direto."
        ),
        "zh": "该模型不使用提供给它的工具。修改将通过直接路径完成。",
    },
    "caps.no_chains": {
        "fr": (
            "Ce modèle sait lire le document mais n'enchaîne pas avec "
            "l'écriture. Les modifications passeront par un chemin direct, "
            "piloté par l'extension — c'est plus fiable."
        ),
        "en": (
            "This model can read the document but does not follow up with "
            "writing. Changes will go through a direct path driven by the "
            "extension — more reliable."
        ),
        "es": (
            "Este modelo sabe leer el documento pero no encadena con la "
            "escritura. Las modificaciones pasarán por una ruta directa "
            "controlada por la extensión — es más fiable."
        ),
        "pt": (
            "Este modelo sabe ler o documento, mas não encadeia com a "
            "escrita. As modificações passarão por um caminho direto, "
            "controlado pela extensão — é mais confiável."
        ),
        "zh": "该模型能读取文档，但不会接着写入。修改将通过由扩展掌控的直接路径完成，这样更可靠。",
    },
    "caps.full": {
        "fr": (
            "Ce modèle enchaîne lecture et écriture : le mode agentique "
            "est pleinement utilisable."
        ),
        "en": (
            "This model chains reading and writing: the agentic mode is "
            "fully usable."
        ),
        "es": (
            "Este modelo encadena lectura y escritura: el modo agéntico es "
            "plenamente utilizable."
        ),
        "pt": (
            "Este modelo encadeia leitura e escrita: o modo agêntico é "
            "plenamente utilizável."
        ),
        "zh": "该模型可以连贯地读写：代理模式完全可用。",
    },
    # --- Page de retour navigateur (callback OAuth) ---
    "callback.title": {
        "fr": "Authentification terminée",
        "en": "Authentication complete",
        "es": "Autenticación completada",
        "pt": "Autenticação concluída",
        "zh": "认证完成",
    },
    "callback.heading": {
        "fr": "Authentification terminée",
        "en": "Authentication complete",
        "es": "Autenticación completada",
        "pt": "Autenticação concluída",
        "zh": "认证完成",
    },
    "callback.badge": {
        "fr": "Connexion validée",
        "en": "Connection validated",
        "es": "Conexión validada",
        "pt": "Conexão validada",
        "zh": "连接已验证",
    },
    "callback.close_tab": {
        "fr": "Vous pouvez fermer cet onglet et revenir à LibreOffice.",
        "en": "You can close this tab and return to LibreOffice.",
        "es": "Puede cerrar esta pestaña y volver a LibreOffice.",
        "pt": "Você pode fechar esta aba e voltar ao LibreOffice.",
        "zh": "您可以关闭此标签页并返回 LibreOffice。",
    },
    "callback.if_stuck": {
        "fr": "Si LibreOffice ne réagit pas, attendez quelques secondes puis relancez l’action.",
        "en": "If LibreOffice does not respond, wait a few seconds and run the action again.",
        "es": "Si LibreOffice no responde, espere unos segundos y vuelva a ejecutar la acción.",
        "pt": "Se o LibreOffice não responder, aguarde alguns segundos e execute a ação novamente.",
        "zh": "如果 LibreOffice 无响应，请等待几秒后重试该操作。",
    },
    "callback.no_action": {
        "fr": "Aucune action supplémentaire n’est requise ici.",
        "en": "No further action is required here.",
        "es": "No se requiere ninguna acción adicional aquí.",
        "pt": "Nenhuma ação adicional é necessária aqui.",
        "zh": "此处无需进一步操作。",
    },
}


_current_locale = DEFAULT_LOCALE


def normalize_locale(raw):
    """Retourne un code de langue supporte, ou None si non reconnu.

    Accepte les formes regionales ("pt-BR", "en_US", "zh-CN.UTF-8").
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    for separator in (".", "@"):
        head, _sep, _tail = candidate.partition(separator)
        if head:
            candidate = head
    candidate = candidate.replace("_", "-")
    primary = candidate.split("-", 1)[0].strip().lower()
    if primary in SUPPORTED:
        return primary
    return None


def set_locale(code):
    """Fixe la langue courante. Retourne le code effectivement retenu."""
    global _current_locale
    normalized = normalize_locale(code)
    _current_locale = normalized or DEFAULT_LOCALE
    return _current_locale


def get_locale():
    return _current_locale


def _uno_ui_locale(ctx=None):
    """Lit la langue de l'interface LibreOffice via /org.openoffice.Setup/L10N.

    Toute erreur est avalee : les tests tournent avec des doublures UNO et
    l'extension doit rester utilisable hors LibreOffice.
    """
    try:
        import uno
        from com.sun.star.beans import PropertyValue as _PV
    except Exception:
        return None
    try:
        context = ctx if ctx is not None else uno.getComponentContext()
        if context is None:
            return None
        provider = context.getServiceManager().createInstanceWithContext(
            "com.sun.star.configuration.ConfigurationProvider", context
        )
        node = _PV()
        node.Name = "nodepath"
        node.Value = "/org.openoffice.Setup/L10N"
        access = provider.createInstanceWithArguments(
            "com.sun.star.configuration.ConfigurationAccess", (node,)
        )
        return normalize_locale(getattr(access, "ooLocale", None))
    except Exception:
        return None


def resolve_locale(ctx=None):
    """Langue de LibreOffice si l'extension la propose, sinon anglais."""
    return _uno_ui_locale(ctx) or FALLBACK_LOCALE


def t(key, **kwargs):
    """Traduit une cle. Repli : langue courante -> francais -> cle brute."""
    entry = CATALOG.get(key)
    if not entry:
        return key
    text = entry.get(_current_locale) or entry.get(DEFAULT_LOCALE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text
