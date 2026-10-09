import { useContext } from 'react';
import { WorkspaceFileContext } from '../workspaces/WorkspaceFileContext';
import { workspaceLink } from '../workspaces/workspaceData';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** No HTML execution or automatic remote image requests in untrusted model output. */
export function Markdown({
  text,
  baseFile,
}: {
  text: string;
  baseFile?: string;
}) {
  const openFile = useContext(WorkspaceFileContext);
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) => {
            const path = href ? workspaceLink(href, baseFile) : null;
            return path && openFile ? (
              <button
                className="artifact-link"
                onClick={() => openFile(path)}
                title={`预览 ${path}`}
              >
                {children}
              </button>
            ) : href && /^(https?:|mailto:)/i.test(href) ? (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            );
          },
          img: ({ alt }) => (
            <span className="muted">[图片：{alt || '未加载'}]</span>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
