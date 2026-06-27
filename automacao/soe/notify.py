"""Envio de e-mail de alerta (Gmail SMTP, STARTTLS 587).

Corpo HTML com KPIs principais, status de validacao (verde/amarelo/vermelho),
tabela de deltas vs execucao anterior e link do dashboard. Falha de forma
graciosa (loga e retorna False) sem derrubar o pipeline.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict

from . import config

logger = logging.getLogger("soe.notify")

_STATUS_COLOR = {"ok": "#1f9d55", "warn": "#d97706", "error": "#dc2626",
                 "verde": "#1f9d55", "amarelo": "#d97706", "vermelho": "#dc2626"}


def build_subject(payload: Dict[str, Any]) -> str:
    meta = payload.get("meta", {})
    val = meta.get("validation", {})
    return (f"Dashboard S&OE atualizado - {meta.get('run_label')} "
            f"({meta.get('week')}) - {val.get('status')}")


def build_html(payload: Dict[str, Any]) -> str:
    meta = payload.get("meta", {})
    kpis = payload.get("kpis", {})
    val = meta.get("validation", {})
    delta = meta.get("delta", {})
    status = val.get("status", "ok")
    health = kpis.get("health_status", "verde")
    color = _STATUS_COLOR.get(status, "#666")
    hcolor = _STATUS_COLOR.get(health, "#666")

    def row(label: str, value: Any, unit: str = "") -> str:
        return (f"<tr><td style='padding:4px 12px;color:#475569'>{label}</td>"
                f"<td style='padding:4px 12px;font-weight:600;text-align:right'>"
                f"{value}{unit}</td></tr>")

    def drow(label: str, value: Any, unit: str) -> str:
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        arrow = "▲" if v > 0 else ("▼" if v < 0 else "■")
        dcolor = "#1f9d55" if v > 0 else ("#dc2626" if v < 0 else "#94a3b8")
        sign = "+" if v > 0 else ""
        return (f"<tr><td style='padding:4px 12px;color:#475569'>{label}</td>"
                f"<td style='padding:4px 12px;text-align:right;color:{dcolor};font-weight:600'>"
                f"{arrow} {sign}{value}{unit}</td></tr>")

    issues = val.get("issues", [])
    issues_html = ""
    if issues:
        items = "".join(
            f"<li style='color:{_STATUS_COLOR.get(i['nivel'], '#666')}'>"
            f"<b>{i['nivel']}</b> [{i['campo']}]: {i['msg']}</li>"
            for i in issues[:12]
        )
        issues_html = (f"<h3 style='margin:18px 0 6px'>Issues de validacao</h3>"
                       f"<ul style='font-size:13px;margin:0;padding-left:18px'>{items}</ul>")

    return f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;background:#f1f5f9;padding:24px;color:#0f172a">
  <div style="max-width:640px;margin:auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)">
    <div style="background:#0f172a;color:#fff;padding:18px 24px">
      <div style="font-size:18px;font-weight:700">Painel S&amp;OE GLB-GFM</div>
      <div style="font-size:13px;color:#cbd5e1">{meta.get('week')} &middot; {meta.get('run_label')} &middot; v{meta.get('version')}</div>
    </div>
    <div style="padding:20px 24px">
      <div style="display:inline-block;padding:6px 14px;border-radius:999px;background:{color};color:#fff;font-size:13px;font-weight:600">
        Validacao: {status.upper()} ({val.get('checks_passed')}/{val.get('checks_total')} checks)
      </div>
      <span style="display:inline-block;margin-left:8px;padding:6px 14px;border-radius:999px;background:{hcolor};color:#fff;font-size:13px;font-weight:600">
        Saude: {health.upper()} ({kpis.get('health_score')})
      </span>

      <h3 style="margin:18px 0 6px">KPIs principais</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px;border:1px solid #e2e8f0;border-radius:8px">
        {row("OTIF", kpis.get('otif_pct'), "%")}
        {row("Fill rate", kpis.get('fill_rate_pct'), "%")}
        {row("Aderencia ao plano", kpis.get('aderencia_plano_pct'), "%")}
        {row("Cobertura media", kpis.get('cobertura_media_dias'), " d")}
        {row("SKUs em alerta", kpis.get('skus_alerta'), "")}
        {row("Gap demanda-suprimento", kpis.get('gap_demanda_suprimento_kt'), " kt")}
        {row("MAPE", kpis.get('mape_pct'), "%")}
        {row("Utilizacao capacidade", kpis.get('utilizacao_capacidade_pct'), "%")}
        {row("EBITDA projetado", kpis.get('ebitda_projetado_mi'), " Mi")}
        {row("Capital imobilizado", kpis.get('capital_imobilizado_mi'), " Mi")}
      </table>

      <h3 style="margin:18px 0 6px">Deltas vs execucao anterior</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px;border:1px solid #e2e8f0;border-radius:8px">
        {drow("OTIF", delta.get('otif_pp'), " pp")}
        {drow("Cobertura", delta.get('cobertura_dias'), " d")}
        {drow("SKUs em alerta", delta.get('skus_alerta'), "")}
      </table>
      <p style="font-size:13px;color:#475569;margin-top:6px">{delta.get('resumo','')}</p>

      {issues_html}

      <div style="margin-top:22px">
        <a href="{config.DASHBOARD_URL}" style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;font-weight:600">Abrir dashboard</a>
      </div>
      <p style="font-size:12px;color:#94a3b8;margin-top:18px">
        Gerado em {meta.get('generated_at_brt')} BRT ({meta.get('generated_at_utc')}) &middot; fonte: {meta.get('source')} &middot; MC: {meta.get('monte_carlo_samples')} amostras.
      </p>
    </div>
  </div>
</body></html>"""


def send_alert(payload: Dict[str, Any]) -> bool:
    """Envia o e-mail. Retorna True em sucesso. Nunca levanta (loga e retorna)."""
    s = config.SETTINGS
    if not s.gmail_user or not s.gmail_pass:
        logger.warning("E-mail nao enviado: SOE_GMAIL_USER/APP_PASSWORD ausentes.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = build_subject(payload)
        msg["From"] = s.gmail_user
        msg["To"] = s.alert_to
        msg.attach(MIMEText(build_html(payload), "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
            server.starttls(context=context)
            server.login(s.gmail_user, s.gmail_pass)
            server.sendmail(s.gmail_user, [s.alert_to], msg.as_string())
        logger.info("E-mail enviado para %s.", s.alert_to)
        return True
    except Exception as exc:  # noqa: BLE001 - falha graciosa por design
        logger.error("Falha ao enviar e-mail (pipeline segue): %s", exc)
        return False
