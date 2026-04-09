import type { RefObject } from "react";

import type { UiMessage } from "../../chat/types";
import { MessageBubble } from "../MessageBubble";

type MessageViewportProps = {
  isAtBottom: boolean;
  isLoading: boolean;
  messages: UiMessage[];
  onJumpToLatest: () => void;
  onScroll: () => void;
  viewportRef: RefObject<HTMLDivElement>;
};

export function MessageViewport({
  isAtBottom,
  isLoading,
  messages,
  onJumpToLatest,
  onScroll,
  viewportRef,
}: MessageViewportProps) {
  return (
    <div className="conversation-viewport-wrap">
      <div className="conversation-viewport" data-testid="chat-messages" onScroll={onScroll} ref={viewportRef}>
        {isLoading ? <div className="conversation-empty">正在加载会话记录...</div> : null}
        {!isLoading && !messages.length ? (
          <div className="conversation-empty conversation-empty--hero">
            <div className="conversation-empty__glyph">&gt;_</div>
            <h3>使用提示</h3>
            <ul className="feature-list conversation-tips">
              <li>直接输入问题即可开始，第一条消息会自动成为会话标题。</li>
              <li>把文件拖进输入框，或点击左下角加号上传图片、PDF、PPTX。</li>
              <li>一个会话在回复时，你仍然可以切换、浏览或新建其他会话。</li>
            </ul>
          </div>
        ) : null}
        {!isLoading ? messages.map((message) => <MessageBubble key={message.id} message={message} />) : null}
      </div>

      {!isAtBottom && messages.length ? (
        <button className="scroll-anchor" type="button" onClick={onJumpToLatest}>
          回到最新消息
        </button>
      ) : null}
    </div>
  );
}
