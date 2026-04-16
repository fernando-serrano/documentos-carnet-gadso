SEL = {
    "login_form": "form#loginform",
    "csrf": 'input[name="csrfmiddlewaretoken"]',
    "username": "#username",
    "password": "#password",
    "submit": "#wp-submit",
    "certificados_dni_input": "#id_dni",
    "certificados_search_button": "button[type='submit']:has-text('Buscar')",
    "certificados_results_table": "div.table-responsive table.table-bordered.table-striped",
    "certificados_results_rows": "div.table-responsive table.table-bordered.table-striped tbody tr",
    "certificados_pdf_link": "a[href*='/pdf/']",
}

LOGIN_ERROR_SELECTORS = [
    ".alert",
    ".alert-danger",
    ".error",
    ".invalid-feedback",
    "#login .text-danger",
    "#login .help-block",
    "#login .message",
]

# Selectores base para la fase de barrido de documentos (etapa siguiente).
DOCUMENT_CANDIDATE_SELECTORS = [
    'a[href$=".pdf"]',
    'a[href*=".pdf?"]',
    'a[download*="pdf" i]',
    'a:has-text("PDF")',
    'button:has-text("PDF")',
    'button:has-text("Descargar")',
    'a:has-text("Descargar")',
]
