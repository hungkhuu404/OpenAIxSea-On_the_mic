export function el(tag, text = '', className = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function quote(text, span, translations, className = '') {
  const block = el('div', '', className);
  const line = el('p');
  const start = span ? text.indexOf(span) : -1;
  if (start < 0) line.textContent = text;
  else {
    line.append(document.createTextNode(text.slice(0, start)));
    const mark = el('button', span, 'highlight');
    mark.type = 'button';
    mark.title = 'Inspect matched span';
    mark.onclick = () => { mark.title = `Matched span: ${span}`; mark.focus(); };
    line.append(mark, document.createTextNode(text.slice(start + span.length)));
  }
  block.append(line);
  const english = translations[text.normalize('NFC')];
  if (english) block.append(el('p', english, 'translation'));
  return block;
}
