import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ComponentPropsWithoutRef, useState } from 'react';

interface ChatMessageProps {
  role: 'user' | 'assistant';
  content: string;
  chartUrls?: string[];
}

export function ChatMessage({ role, content, chartUrls }: ChatMessageProps) {
  const isUser = role === 'user';
  const [failedImages, setFailedImages] = useState<Set<number>>(new Set());

  const handleImageError = (index: number) => {
    setFailedImages((prev) => new Set(prev).add(index));
  };

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 ${
          isUser
            ? 'bg-chat-user border border-border'
            : 'bg-chat-assistant'
        }`}
      >
        {isUser ? (
          <p className="text-foreground whitespace-pre-wrap">{content}</p>
        ) : (
          <div className="markdown-content text-foreground">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                table: ({ children, ...props }: ComponentPropsWithoutRef<'table'>) => (
                  <div className="table-wrapper overflow-auto max-h-[280px] my-4">
                    <table {...props}>{children}</table>
                  </div>
                ),
                img: ({ src, alt, ...props }: ComponentPropsWithoutRef<'img'>) => (
                  <img
                    src={src}
                    alt={alt || 'Chart'}
                    className="max-w-full rounded-lg border border-border my-3"
                    {...props}
                  />
                ),
              }}
            >
              {content}
            </ReactMarkdown>
            {chartUrls && chartUrls.length > 0 && (
              <div className="mt-3 space-y-3">
                {chartUrls.map((url, idx) =>
                  failedImages.has(idx) ? (
                    <div
                      key={idx}
                      className="p-4 rounded-lg bg-muted text-muted-foreground text-sm"
                    >
                      Failed to load chart image
                    </div>
                  ) : (
                    <a key={idx} href={url} target="_blank" rel="noreferrer" title="Open full size">
                      <img
                        src={url}
                        alt={`Chart ${idx + 1}`}
                        className="max-w-full rounded-lg border border-border bg-white"
                        onError={() => handleImageError(idx)}
                      />
                    </a>
                  )
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
