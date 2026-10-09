import { useState } from 'react';
import { ChevronDown, GitBranch } from 'lucide-react';
import { SessionTimeline } from './SessionTimeline';
import { MemberPermission } from './PermissionControl';
import { statusLabel, toolArguments } from './timeline';
import type { Message } from './native';
import type { NativeTask, SessionView } from './nativeApi';

export type TeamContext = {
  members: NonNullable<SessionView['team']>['members'];
  statuses?: Record<string, string>;
  limit: number;
};
export function TeamPanel({
  team,
  user,
  messages,
  tasks,
  onInspect,
  anchorId,
}: {
  team: TeamContext;
  user: string;
  messages: Message[];
  tasks: NativeTask[];
  onInspect: (value: unknown) => void;
  anchorId: string;
}) {
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const invitations = messages.flatMap((m) =>
    m.content
      .filter((b) => b.type === 'tool_call' && b.name === 'AgentCreate')
      .map(toolArguments),
  );
  const busy = team.members.filter(
    (m) =>
      m.session_id &&
      ['running', 'preparing'].includes(team.statuses?.[m.session_id] ?? ''),
  ).length;
  return (
    <section className="team-panel" id={anchorId} aria-label="子 Agent 协作">
      <header>
        <GitBranch size={17} />
        <strong>协作分工</strong>
        <span>
          {team.members.length} / {team.limit} 位成员
        </span>
        <small>{busy ? `${busy} 位执行中` : '展开查看各自的执行记录'}</small>
      </header>
      {!team.members.length && (
        <p className="team-empty">
          已发起委派，尚无已确认的成员。创建结果见工具记录。
        </p>
      )}
      {team.members.map((member) => {
        const sid = member.session_id;
        const name = member.agent.data.name;
        const invitation = invitations.find((i) => i.name === name);
        const owned = tasks.filter((t) => t.owner === name);
        const state = sid ? team.statuses?.[sid] : undefined;
        const description =
          typeof invitation?.description === 'string'
            ? invitation.description
            : owned.map((t) => t.subject).join('；');
        return (
          <details
            className="team-member"
            key={member.agent.id}
            onToggle={(e) => {
              const open = e.currentTarget.open;
              setOpened((current) => {
                const next = new Set(current);
                if (open) next.add(member.agent.id);
                else next.delete(member.agent.id);
                return next;
              });
            }}
          >
            <summary>
              <span className={`member-indicator ${state ?? ''}`} />
              <span className="member-title">
                <strong>{name}</strong>
                <small>{description || '任务说明见成员会话'}</small>
              </span>
              <span className={`member-status ${state ?? ''}`}>
                {statusLabel(state)}
              </span>
              <ChevronDown size={14} />
            </summary>
            <div className="team-member-detail">
              {owned.length > 0 && (
                <p className="member-review">
                  主 Agent 已验收{' '}
                  {owned.filter((t) => t.state === 'completed').length} /{' '}
                  {owned.length} 项待办
                </p>
              )}
              {typeof invitation?.prompt === 'string' && (
                <details className="delegation-brief">
                  <summary>委派任务与验收要求</summary>
                  <p>{invitation.prompt}</p>
                </details>
              )}
              {sid && opened.has(member.agent.id) ? (
                <>
                <MemberPermission key={sid} user={user} session={{id: sid, agent_id: member.agent.id}}
                  status={state} name={name} />
                <SessionTimeline
                  user={user}
                  session={{ id: sid, agent_id: member.agent.id }}
                  label={name}
                  onInspect={onInspect}
                  running={
                    state ? ['running', 'preparing'].includes(state) : false
                  }
                />
                </>
              ) : (
                !sid && <p className="muted">成员会话尚未就绪。</p>
              )}
            </div>
          </details>
        );
      })}
    </section>
  );
}
