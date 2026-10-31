import { jsPDF } from "jspdf";
import type { TripPlan } from "../types/trip";
import {
  buildTripDocument,
  type DocumentBlockKind as BlockKind,
  type DocumentBlock as TextBlock,
} from "../utils/trip_document";

interface DrawLine {
  text: string;
  kind: BlockKind;
  y: number;
}
interface TextStyle {
  size: number;
  lineHeight: number;
  weight: number;
  color: string;
  before: number;
  after: number;
}

const WIDTH = 794;
const HEIGHT = 1123;
const SCALE = 2;
const MARGIN = 54;
const CONTENT_TOP = 102;
const CONTENT_BOTTOM = HEIGHT - 74;
const FONT = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';
const styles: Record<BlockKind, TextStyle> = {
  title: {
    size: 28,
    lineHeight: 42,
    weight: 600,
    color: "#174f40",
    before: 0,
    after: 16,
  },
  heading: {
    size: 20,
    lineHeight: 32,
    weight: 600,
    color: "#174f40",
    before: 18,
    after: 10,
  },
  subheading: {
    size: 15,
    lineHeight: 25,
    weight: 600,
    color: "#315c47",
    before: 12,
    after: 6,
  },
  body: {
    size: 13,
    lineHeight: 23,
    weight: 400,
    color: "#34473c",
    before: 0,
    after: 8,
  },
  muted: {
    size: 11,
    lineHeight: 20,
    weight: 400,
    color: "#637365",
    before: 0,
    after: 6,
  },
  warning: {
    size: 12,
    lineHeight: 22,
    weight: 400,
    color: "#88652f",
    before: 4,
    after: 8,
  },
};
function safeFilenamePart(value: string): string {
  return Array.from(
    value.replace(/[\u0000-\u001f\u007f<>:"/\\|?*]/g, "").trim(),
  )
    .slice(0, 60)
    .join("")
    .replace(/[. ]+$/g, "");
}

export function tripPdfFilename(plan: TripPlan): string {
  return `${safeFilenamePart(plan.city) || "旅行"}-${safeFilenamePart(plan.start_date) || "行程"}-旅行计划.pdf`;
}

function setTextStyle(context: CanvasRenderingContext2D, kind: BlockKind) {
  const style = styles[kind];
  context.font = `${style.weight} ${style.size}px ${FONT}`;
  context.fillStyle = style.color;
  context.textBaseline = "top";
}

function wrapText(context: CanvasRenderingContext2D, text: string): string[] {
  const lines: string[] = [];
  for (const paragraph of text
    .replace(/\r\n?/g, "\n")
    .replace(/\t/g, "    ")
    .split("\n")) {
    let current = "";
    // 按Unicode字符断行，中文和连续英文均能换行，不拆开代理对。
    for (const character of Array.from(paragraph)) {
      if (
        current &&
        context.measureText(current + character).width > WIDTH - MARGIN * 2
      ) {
        lines.push(current);
        current = character;
      } else current += character;
    }
    lines.push(current);
  }
  return lines;
}

function paginate(
  context: CanvasRenderingContext2D,
  blocks: TextBlock[],
): DrawLine[][] {
  const pages: DrawLine[][] = [[]];
  let current = pages[0]!;
  let y = CONTENT_TOP;
  const newPage = () => {
    current = [];
    pages.push(current);
    y = CONTENT_TOP;
  };
  for (const block of blocks) {
    if (block.pageBreakBefore && current.length) newPage();
    setTextStyle(context, block.kind);
    const lines = wrapText(context, block.text);
    const style = styles[block.kind];
    // 标题至少与下一行正文一起留在页面上，普通段落也尽量保留前两行。
    const isHeading = ["title", "heading", "subheading"].includes(block.kind);
    const reserve = isHeading
      ? lines.length * style.lineHeight + style.after + styles.body.lineHeight
      : Math.min(2, lines.length) * style.lineHeight;
    if (current.length && y + style.before + reserve > CONTENT_BOTTOM)
      newPage();
    y += current.length ? style.before : 0;
    for (const line of lines) {
      if (y + style.lineHeight > CONTENT_BOTTOM) newPage();
      current.push({ text: line, kind: block.kind, y });
      y += style.lineHeight;
    }
    y += style.after;
  }
  return pages;
}

export async function createTripPdf(
  plan: TripPlan,
  onProgress?: (page: number, total: number) => void,
): Promise<Blob> {
  if (!plan.days.length) throw new Error("没有可导出的行程。");
  // 先读取整份数据，避免用户在等待绘制时切换日期影响导出。
  const blocks = buildTripDocument(plan);
  await document.fonts.ready;
  const canvas = document.createElement("canvas");
  canvas.width = WIDTH * SCALE;
  canvas.height = HEIGHT * SCALE;
  try {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器无法绘制PDF页面。");
    const pages = paginate(context, blocks);
    const pdf = new jsPDF({
      orientation: "portrait",
      unit: "mm",
      format: "a4",
      compress: true,
    });
    // 元数据采用固定文字；行程中的外部文本只作为Canvas文字绘制。
    pdf.setProperties({ title: "Travel itinerary", creator: "Trip Planner" });
    for (let index = 0; index < pages.length; index++) {
      onProgress?.(index + 1, pages.length);
      await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
      context.setTransform(SCALE, 0, 0, SCALE, 0, 0);
      context.fillStyle = "#ffffff";
      context.fillRect(0, 0, WIDTH, HEIGHT);
      context.strokeStyle = "#dce5d9";
      context.lineWidth = 1;
      context.beginPath();
      context.moveTo(MARGIN, 70);
      context.lineTo(WIDTH - MARGIN, 70);
      context.stroke();
      setTextStyle(context, "subheading");
      context.fillText("下一程 / 旅行规划", MARGIN, 34);
      for (const line of pages[index]!) {
        setTextStyle(context, line.kind);
        context.fillText(line.text, MARGIN, line.y);
      }
      setTextStyle(context, "muted");
      context.fillText("旅行建议，请出行前确认实际条件。", MARGIN, HEIGHT - 40);
      const pageNumber = `第 ${index + 1} / ${pages.length} 页`;
      context.fillText(
        pageNumber,
        WIDTH - MARGIN - context.measureText(pageNumber).width,
        HEIGHT - 40,
      );
      if (index > 0) pdf.addPage();
      pdf.addImage(
        canvas.toDataURL("image/jpeg", 0.94),
        "JPEG",
        0,
        0,
        210,
        297,
      );
    }
    return pdf.output("blob");
  } finally {
    // 逐页复用同一个Canvas，最后释放像素缓冲，控制长行程导出的内存。
    canvas.width = 0;
    canvas.height = 0;
  }
}
