import { Fingerprint } from 'lucide-react';
import { RawDetails } from './SessionTimeline';
import type { Block } from './native';

type EvidenceInspection = {
  kind: 'evidence';
  input: Record<string, unknown>;
  result: Block;
};
function isEvidence(value: unknown): value is EvidenceInspection {
  return (
    !!value &&
    typeof value === 'object' &&
    'kind' in value &&
    value.kind === 'evidence' &&
    'input' in value &&
    'result' in value
  );
}
function evidenceData(block: Block): Record<string, unknown> {
  const output = Array.isArray(block.output)
    ? block.output
    : [{ text: block.output }];
  for (const item of output) {
    try {
      const data = JSON.parse(item.text);
      if (
        data &&
        typeof data === 'object' &&
        typeof data.id === 'string' &&
        data.id.startsWith('E-')
      )
        return data;
    } catch {
      /* Unstructured results remain available below. */
    }
  }
  return {};
}
export function RecordInspector({ value }: { value: unknown }) {
  if (!isEvidence(value))
    return value ? (
      <pre className="inspector-json">{JSON.stringify(value, null, 2)}</pre>
    ) : (
      <p className="muted">点击消息详情或证据索引查看原始记录。</p>
    );
  const record = evidenceData(value.result);
  const text = (key: string) =>
    String(value.input[key] ?? record[key] ?? '未标注');
  return (
    <section className="evidence-detail">
      <div className="eyebrow">
        <Fingerprint size={16} />
        证据记录
      </div>
      <h2>{String(record.id ?? '已登记')}</h2>
      <p>{text('summary')}</p>
      <dl>
        <dt>来源</dt>
        <dd>{text('source')}</dd>
        <dt>产物位置</dt>
        <dd>
          {String(record.location ?? value.input.artifact_path ?? '未标注')}
        </dd>
        <dt>产物 SHA-256</dt>
        <dd>{text('sha256')}</dd>
        <dt>登记时的置信标记</dt>
        <dd>{text('confidence')}</dd>
      </dl>
      <p className="muted">
        登记与置信标记来自执行记录；是否支持结论，应结合原始工具结果和主 Agent
        的复核。
      </p>
      <RawDetails label="完整证据输入与结果" value={value} />
    </section>
  );
}
