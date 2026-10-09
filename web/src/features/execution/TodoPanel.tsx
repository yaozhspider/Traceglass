import { ChevronDown } from 'lucide-react';
import type { NativeTask } from './nativeApi';

const stateLabels: Record<NativeTask['state'], string> = {
  pending: '待开始',
  in_progress: '执行中',
  completed: '已完成',
};

export function TodoPanel({ tasks }: { tasks: NativeTask[] }) {
  if (!tasks.length) return null;

  const completed = tasks.filter((task) => task.state === 'completed').length;
  const inProgress = tasks.filter(
    (task) => task.state === 'in_progress',
  ).length;
  const byId = new Map(tasks.map((task) => [task.id, task]));
  const describeTask = (id: string) =>
    `#${id}${byId.has(id) ? ` · ${byId.get(id)!.subject}` : ''}`;

  return (
    <details className="todo-panel" aria-label="当前计划">
      <summary className="todo-panel-heading">
        <div>
          <h2>当前计划</h2>
          <p>
            {completed} / {tasks.length} 项已完成
            {inProgress > 0 ? ` · ${inProgress} 项执行中` : ''}
          </p>
        </div>
        <span className="todo-panel-count">{tasks.length} 项任务</span>
      </summary>
      <ol className="todo-list">
        {tasks.map((task) => (
          <li key={task.id}>
            <details className={`todo-task todo-task-${task.state}`}>
              <summary>
                <span className={`todo-state todo-state-${task.state}`}>
                  {stateLabels[task.state]}
                </span>
                <span className="todo-task-main">
                  <strong>{task.subject}</strong>
                  <span className="todo-task-meta">
                    #{task.id} ·{' '}
                    {task.owner ? `负责人：${task.owner}` : '待分配'}
                    {task.blocked_by.length > 0
                      ? ` · 前置：${task.blocked_by.map((id) => `#${id}`).join('、')}`
                      : ''}
                  </span>
                </span>
                <ChevronDown
                  className="todo-chevron"
                  size={15}
                  aria-hidden="true"
                />
              </summary>
              <div className="todo-task-details">
                <p>{task.description || '暂无任务描述。'}</p>
                {(task.blocked_by.length > 0 || task.blocks.length > 0) && (
                  <dl>
                    {task.blocked_by.length > 0 && (
                      <>
                        <dt>前置任务</dt>
                        <dd>
                          {task.blocked_by.map((id) => (
                            <span key={id}>{describeTask(id)}</span>
                          ))}
                        </dd>
                      </>
                    )}
                    {task.blocks.length > 0 && (
                      <>
                        <dt>后续任务</dt>
                        <dd>
                          {task.blocks.map((id) => (
                            <span key={id}>{describeTask(id)}</span>
                          ))}
                        </dd>
                      </>
                    )}
                  </dl>
                )}
              </div>
            </details>
          </li>
        ))}
      </ol>
      <details className="raw-details todo-native-data">
        <summary>原生任务数据</summary>
        <pre>{JSON.stringify(tasks, null, 2)}</pre>
      </details>
    </details>
  );
}
