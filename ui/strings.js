export const verdicts = {
  green: { label: 'Within file', shape: 'circle', symbol: '✓' },
  yellow: { label: 'Beyond file', shape: 'box', symbol: '↗' },
  red: { label: 'Prohibited', shape: 'octagon', symbol: '×' },
  abstain: { label: 'Needs human', shape: 'dashed', symbol: '?' },
};
export const steps = {
  S0: 'Context', S1: 'Extract claims', A: 'Allow list', B: 'Rules',
  C: 'Channel', D: 'Dossier checks', E: 'Two votes', F: 'Legal basis', S3: 'Aggregate',
};
