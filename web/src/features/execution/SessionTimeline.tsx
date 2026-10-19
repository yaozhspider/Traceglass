import { Fragment, useEffect, useMemo, useState } from 'react';
import {
  ArrowDown,
  Check,
  ChevronRight,
  Fingerprint,
  GitBranch,
  Wrench,
} from 'lucide-react';
import {
  confirmationKey,
  hasRecordedUserInput,
  isSampleContext,
  projectTasks,
  replyErrorMessage,
  replyFinishedLabel,
} from './native';
import { useSessionFeed } from './useSessionFeed';
import { TodoPanel } from './TodoPanel';
import { TeamPanel } from './TeamPanel';
import type { TeamContext } from './TeamPanel';
import { Markdown } from './Markdown';
import {
  delegationAnchor,
  evidenceTools,
  timelineBlocks,
  toolArguments,
  toolStatus,
} from './timeline';
import { request, sessionPath } from './nativeApi';
import { BUDGET_CLOSEOUT_SOURCE, canResumeReply, hasBudgetCloseout } from './controls';
import type { NativeTask, SessionRef } from './nativeApi';
import type { Block, Confirmation } from './native';

export function RawDetails({
  label,
  value,
}: {
  label: string;
  value: unknown;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="raw-details"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>{label}</summary>
      {open && (
        <pre>
          {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
        </pre>
      )}
    </details>
  );
}
export function BlockView({
  block,
  result,
  foldSample = false,
  running,
}: {
  block: Block;
  result?: Block;
  foldSample?: boolean;
  running?: boolean;
}) {
  if (block.type === 'text' && foldSample && isSampleContext(block.text))
    return <RawDetails label="对话附件 · 文件与路径" value={block.text} />;
  if (block.type === 'text') return <Markdown text={block.text ?? ''} />;
  if (block.type === 'hint' && block.source === BUDGET_CLOSEOUT_SOURCE)
    return <p className="reply-status">已进入阶段性收尾 · 根据已有证据总结结论与未完成事项</p>;
  if (block.type === 'thinking')
    return (
      <details className="thinking-card" open>
        <summary>分析过程</summary>
        <p className="message-text">
          {block.thinking || '模型未提供可显示的分析文本'}
        </p>
      </details>
    );
  if (block.type === 'tool_call' || block.type === 'tool_result') {
    const args = toolArguments(block);
    const brief = Object.entries(args).find(
      ([key, value]) =>
        [
          'command',
          'path',
          'sample_path',
          'name',
          'subject',
          'skill_name',
          'file_path',
        ].includes(key) && typeof value === 'string',
    );
    const status = toolStatus(
      block,
      result ?? (block.type === 'tool_result' ? block : undefined),
      running,
    );
    return (
      <details
        className={`tool-card tool-${status === '失败' ? 'error' : status === '完成' ? 'success' : 'pending'}`}
      >
        <summary>
          <ChevronRight size={12} />
          {status === '完成' ? <Check size={13} /> : <Wrench size={13} />}
          <strong>{block.name || '工具结果'}</strong>
          <span className="tool-brief">
            {brief
              ? String(brief[1])
              : Object.keys(args).length
                ? `${Object.keys(args).length} 个参数`
                : ''}
          </span>
          <small>{status}</small>
        </summary>
        {block.type === 'tool_call' && (
          <div className="tool-payload">
            <span>输入</span>
            <pre>
              {typeof block.input === 'string'
                ? block.input
                : JSON.stringify(block.input, null, 2)}
            </pre>
          </div>
        )}
        {(result || block.type === 'tool_result') && (
          <div className="tool-payload">
            <span>输出</span>
            <pre>
              {JSON.stringify(
                (result ?? block).output ?? (result ?? block).data,
                null,
                2,
              )}
            </pre>
          </div>
        )}
      </details>
    );
  }
  return (
    <RawDetails
      label={block.type === 'hint' ? '团队消息 / 提示' : block.type}
      value={block}
    />
  );
}

type Props = {
  user: string;
  session: SessionRef;
  label: string;
  onInspect: (value: unknown) => void;
  initialTasks?: NativeTask[];
  tasksSettledAt?: number;
  running?: boolean;
  pendingInput?: string;
  pendingInputId?: string;
  onInputRecorded?: () => void;
  team?: TeamContext;
};
export function SessionTimeline({
  user,
  session,
  label,
  onInspect,
  initialTasks,
  tasksSettledAt = 0,
  running,
  pendingInput,
  pendingInputId,
  onInputRecorded,
  team,
}: Props) {
  const feed = useSessionFeed(user, session);
  const tasks = useMemo(
    () =>
      initialTasks === undefined
        ? undefined
        : projectTasks(initialTasks, feed.events, {
            snapshotSettledAt: tasksSettledAt,
            lastTaskUpdateAt: feed.lastTaskUpdateAt,
          }),
    [initialTasks, feed.events, tasksSettledAt, feed.lastTaskUpdateAt],
  );
  const anchor = delegationAnchor(feed.messages);
  const evidence = evidenceTools(feed.messages);
  const teamId = `team-${session.id}`;
  const showTeam = !!team && (!!anchor || team.members.length > 0);
  const teamPanel = team && (
    <TeamPanel
      team={team}
      user={user}
      messages={feed.messages}
      tasks={tasks ?? []}
      onInspect={onInspect}
      anchorId={teamId}
    />
  );
  const inputRecorded = hasRecordedUserInput(
    feed.messages,
    pendingInput,
    pendingInputId,
  );
  useEffect(() => {
    if (inputRecorded) onInputRecorded?.();
  }, [inputRecorded, onInputRecorded]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<Set<string>>(new Set());
  const [resumedReply, setResumedReply] = useState<string | null>(null);
  const lastReply = feed.messages.at(-1);
  const resumable = canResumeReply(lastReply);
  const resume = async () => {
    if (busy || !lastReply || resumedReply === lastReply.id) return;
    setBusy(true);
    setError(null);
    try {
      await request('/chat/', user, {method: 'POST', body: JSON.stringify({
        agent_id: session.agent_id, session_id: session.id, input: null,
      })});
      setResumedReply(lastReply.id);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const confirm = async (pending: Confirmation, confirmed: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await request('/chat/', user, {
        method: 'POST',
        body: JSON.stringify({
          agent_id: session.agent_id,
          session_id: session.id,
          input: {
            type: 'USER_CONFIRM_RESULT',
            reply_id: pending.reply_id,
            confirm_results: pending.tool_calls.map((tool_call) => ({
              tool_call,
              confirmed,
            })),
          },
        }),
      });
      setSubmitted((s) => new Set([...s, confirmationKey(pending)]));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const interrupt = async () => {
    try {
      await request('/product' + sessionPath(session, 'interrupt'), user, {
        method: 'POST',
      });
    } catch (e) {
      setError(String(e));
    }
  };
  const jump = (id: string) =>
    document
      .getElementById(id)
      ?.scrollIntoView({ block: 'start', behavior: 'instant' });
  return (
    <section className="session-timeline">
      <div className="timeline-heading">
        <span>
          <i
            className={
              feed.connected ? 'connection-dot connected' : 'connection-dot'
            }
          />
          {label} · {feed.connected ? '实时同步' : '重新连接中'}
        </span>
        {(running || feed.confirmations.length > 0) && (
          <button className="secondary-button" onClick={() => void interrupt()}>
            暂停此 Agent
          </button>
        )}
      </div>
      {team && (
        <div className="analysis-overview">
          <button disabled={!showTeam} onClick={() => jump(teamId)}>
            <GitBranch size={14} />
            {team.members.length
              ? `${team.members.length} 位子 Agent`
              : anchor
                ? '委派请求已发起'
                : '主 Agent 分析 · 尚未委派'}
          </button>
          <span>
            <Fingerprint size={14} />
            {evidence.length} 条主会话证据
          </span>
          <button onClick={() => jump(`latest-${session.id}`)}>
            <ArrowDown size={14} />
            最新进展
          </button>
        </div>
      )}
      {feed.confirmations.length > 0 && (
        <button
          className="permission-jump"
          onClick={() => jump(`permission-${session.id}`)}
        >
          有 {feed.confirmations.length} 项工具授权待处理 · 查看参数
        </button>
      )}
      {tasks && <TodoPanel tasks={tasks} />}
      {feed.error && <div className="error-banner">{feed.error}</div>}
      {error && <div className="error-banner">{error}</div>}
      {resumable && lastReply && !running && (
        <div className="execution-resume">
          <span>{lastReply.finished_reason === 'error' ? '本轮执行失败，历史与待办已保留，可重试继续。'
            : lastReply.finished_reason === 'exceed_max_iters' || hasBudgetCloseout(lastReply)
            ? '本轮执行预算已用完，任务与记录已保留，可继续分析。' : '本轮已暂停，任务与记录已保留。'}</span>
          <button type="button" disabled={busy || resumedReply === lastReply.id} onClick={() => void resume()}>
            {resumedReply === lastReply.id ? '正在继续…' : '继续执行'}
          </button>
        </div>
      )}
      {feed.confirmations.map((p, index) => (
        <div
          className="confirmation-card"
          id={index === 0 ? `permission-${session.id}` : undefined}
          key={confirmationKey(p)}
        >
          <strong>
            需要你确认 · {p.tool_calls.map((t) => t.name).join('、')}
          </strong>
          <p>查看工具参数后，选择允许本次执行或拒绝。</p>
          <pre>{JSON.stringify(p.tool_calls, null, 2)}</pre>
          {submitted.has(confirmationKey(p)) ? (
            <p>确认已提交，等待 Agent 更新。</p>
          ) : (
            <div className="button-row">
              <button disabled={busy} onClick={() => void confirm(p, true)}>
                允许本次
              </button>
              <button disabled={busy} onClick={() => void confirm(p, false)}>
                拒绝
              </button>
            </div>
          )}
        </div>
      ))}
      {showTeam && !anchor && teamPanel}
      {feed.messages.map((message) => (
        <article
          className={`message-card ${message.role === 'user' ? 'user-message' : ''}`}
          key={message.id}
        >
          <div className="message-heading">
            <strong>
              {message.role === 'user' ? '你' : message.name || label}
            </strong>
            <button
              className="icon-button"
              onClick={() =>
                onInspect({
                  message,
                  events: feed.events.filter((e) => e.reply_id === message.id),
                })
              }
              aria-label="查看完整消息"
            >
              详情
            </button>
          </div>
          {timelineBlocks(message.content).map(({ block, result, index }) => (
            <Fragment key={`${block.type}:${block.id ?? index}`}>
              <BlockView
                block={block}
                result={result}
                foldSample={message.role === 'user'}
                running={running && !message.finished_reason}
              />
              {showTeam &&
                anchor?.messageId === message.id &&
                anchor.blockIndex === index &&
                teamPanel}
            </Fragment>
          ))}
          {message.error != null && (
            <div className="error-banner" role="alert">
              {replyErrorMessage(message.error)}
            </div>
          )}
          {message.finished_reason &&
            message.finished_reason !== 'completed' && (
              <small className="reply-status">
                回复状态：{replyFinishedLabel(message.finished_reason)}
              </small>
            )}
        </article>
      ))}
      {pendingInput && !inputRecorded && (
        <article className="message-card user-message">
          <div className="message-heading">
            <strong>你</strong>
            <small>
              {running === false
                ? '未写入会话 · 问题已保留'
                : '发送中 · 等待环境准备'}
            </small>
          </div>
          <p className="message-text">{pendingInput}</p>
        </article>
      )}
      {!feed.messages.length && !pendingInput && (
        <div className="empty-trace">
          分析过程、工具输入输出与协作分工会显示在这里。
        </div>
      )}
      {evidence.length > 0 && (
        <details className="evidence-index">
          <summary>
            <Fingerprint size={15} />
            证据索引 · {evidence.length} 条登记记录
          </summary>
          <p>登记表示已保存，证据是否支持结论仍以复核为准。</p>
          {evidence.map((item) => {
            const args = toolArguments(item.call);
            return (
              <button
                key={`${item.messageId}:${item.call.id}`}
                onClick={() =>
                  onInspect({
                    kind: 'evidence',
                    input: args,
                    result: item.result,
                  })
                }
              >
                {String(
                  args.title ??
                    args.claim ??
                    args.summary ??
                    args.description ??
                    item.call.id,
                )}
                <ChevronRight size={14} />
              </button>
            );
          })}
        </details>
      )}
      <div id={`latest-${session.id}`} />
      <RawDetails
        label={`原始事件 · ${feed.events.length}`}
        value={feed.events}
      />
    </section>
  );
}
