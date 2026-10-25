export const POPULAR = Object.freeze(['https://www.google.com/','https://github.com/','https://www.wikipedia.org/','https://www.cloudflare.com/','https://www.microsoft.com/','https://www.youtube.com/']);
export function parseUrls(text) {
  const values = text.split(/\r?\n/).map(value=>value.trim()).filter(Boolean);
  return [...new Set(values)];
}
export function resultText(row) {
  return `${row.url || row.input}\n${row.label}${row.status ? ` · HTTP ${row.status}` : ''}${Number.isFinite(row.elapsed_ms) ? ` · ${row.elapsed_ms} ms` : ''}\n${row.detail || ''}${row.final_url && row.final_url !== row.url ? `\nFinal URL: ${row.final_url}` : ''}${row.checked_at ? `\nChecked: ${row.checked_at}` : ''}`;
}
export function csvReport(rows) {
  const quote=value=>{
    let text=String(value ?? '');
    if (/^[\s]*[=+\-@]/.test(text)) text="'"+text;
    return '"'+text.replaceAll('"','""')+'"';
  };
  return [['URL','HTTP status','Result','Response time (ms)','Final URL','Checked UTC','Detail'],...rows.map(r=>[r.url||r.input,r.status,r.label,r.elapsed_ms,r.final_url,r.checked_at,r.detail])].map(row=>row.map(quote).join(',')).join('\r\n');
}
