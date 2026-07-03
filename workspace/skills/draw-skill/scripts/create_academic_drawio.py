#!/usr/bin/env python3
"""Create black-and-white academic draw.io diagrams for common software engineering views."""

from __future__ import annotations

import argparse
import html
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


FONT = "fontFamily=SimSun;fontSize=12;fontColor=#000000;"
PADDING = "spacing=4;spacingTop=3;spacingRight=4;spacingBottom=3;spacingLeft=4;"
COMPACT_PADDING = "spacing=2;spacingTop=2;spacingRight=2;spacingBottom=2;spacingLeft=2;"
NODE = "whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1;" + FONT + PADDING
COMPACT_NODE = "whiteSpace=wrap;html=1;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1;" + FONT + COMPACT_PADDING
TEXT = "text;html=1;strokeColor=none;fillColor=none;" + FONT
EDGE = (
    "html=1;rounded=0;orthogonalLoop=1;jettySize=auto;"
    "strokeColor=#000000;strokeWidth=1;" + FONT
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def workspace_root() -> Path:
    raw = os.getenv("GATEWAY_WORKSPACE_ROOT", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (repo_root() / candidate).resolve()
    return (repo_root() / "workspace").resolve()


def diagrams_dir() -> Path:
    return (workspace_root() / "reports" / "diagrams").resolve()


def resolve_output(raw_path: str | Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        if candidate.parts and candidate.parts[0] == "workspace":
            candidate = repo_root() / candidate
        else:
            candidate = workspace_root() / candidate
    output = candidate.resolve()
    try:
        output.relative_to(diagrams_dir())
    except ValueError as exc:
        raise SystemExit("output must be inside workspace/reports/diagrams") from exc
    if output.suffix.lower() != ".drawio":
        raise SystemExit("output file must end with .drawio")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


class Diagram:
    def __init__(self, name: str, width: int = 1169, height: int = 827) -> None:
        self.next_id = 2
        self.root = ET.Element("root")
        ET.SubElement(self.root, "mxCell", id="0")
        ET.SubElement(self.root, "mxCell", id="1", parent="0")
        self.mxfile = ET.Element(
            "mxfile",
            host="app.diagrams.net",
            agent="draw-skill",
            version="24.7.17",
        )
        self.diagram = ET.SubElement(self.mxfile, "diagram", id=name, name=name)
        self.model = ET.SubElement(
            self.diagram,
            "mxGraphModel",
            dx="1200",
            dy="800",
            grid="1",
            gridSize="10",
            guides="1",
            tooltips="1",
            connect="1",
            arrows="1",
            fold="1",
            page="1",
            pageScale="1",
            pageWidth=str(width),
            pageHeight=str(height),
            math="0",
            shadow="0",
        )
        self.model.append(self.root)

    def _id(self, prefix: str) -> str:
        value = f"{prefix}{self.next_id}"
        self.next_id += 1
        return value

    def vertex(self, value: str, x: int, y: int, w: int, h: int, style: str, prefix: str = "v") -> str:
        cell_id = self._id(prefix)
        cell = ET.SubElement(
            self.root,
            "mxCell",
            id=cell_id,
            value=value,
            style=style,
            vertex="1",
            parent="1",
        )
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
        return cell_id

    def edge(self, source: str, target: str, value: str = "", style: str | None = None, prefix: str = "e") -> str:
        cell_id = self._id(prefix)
        cell = ET.SubElement(
            self.root,
            "mxCell",
            id=cell_id,
            value=value,
            style=style or (EDGE + "endArrow=block;"),
            edge="1",
            parent="1",
            source=source,
            target=target,
        )
        ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        return cell_id

    def routed_edge(
        self,
        source: str,
        target: str,
        value: str = "",
        points: list[tuple[int, int]] | None = None,
        style: str | None = None,
        prefix: str = "e",
    ) -> str:
        cell_id = self._id(prefix)
        cell = ET.SubElement(
            self.root,
            "mxCell",
            id=cell_id,
            value=value,
            style=style or (EDGE + "endArrow=block;"),
            edge="1",
            parent="1",
            source=source,
            target=target,
        )
        geom = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        if points:
            array = ET.SubElement(geom, "Array", **{"as": "points"})
            for x, y in points:
                ET.SubElement(array, "mxPoint", x=str(x), y=str(y))
        return cell_id

    def point_edge(self, value: str, x1: int, y1: int, x2: int, y2: int, style: str, prefix: str = "e") -> str:
        cell_id = self._id(prefix)
        cell = ET.SubElement(
            self.root,
            "mxCell",
            id=cell_id,
            value=value,
            style=style,
            edge="1",
            parent="1",
        )
        geom = ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        ET.SubElement(geom, "mxPoint", x=str(x1), y=str(y1), **{"as": "sourcePoint"})
        ET.SubElement(geom, "mxPoint", x=str(x2), y=str(y2), **{"as": "targetPoint"})
        return cell_id

    def title(self, text: str, x: int = 420, y: int = 30, w: int = 330) -> str:
        return self.vertex(text, x, y, w, 30, TEXT + "fontSize=14;fontStyle=1;", "t")

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tree = ET.ElementTree(self.mxfile)
        ET.indent(tree, space="  ")
        tree.write(path, encoding="utf-8", xml_declaration=False)


def flowchart(path: Path) -> None:
    d = Diagram("flowchart")
    d.title("流程图：登录校验", x=485, y=35, w=200)
    start = d.vertex("开始", 80, 190, 90, 38, NODE + "rounded=1;arcSize=50;")
    input_ = d.vertex("输入账号和密码", 220, 186, 150, 46, NODE + "shape=parallelogram;perimeter=parallelogramPerimeter;fixedSize=1;")
    check = d.vertex("格式正确？", 420, 174, 112, 70, NODE + "rhombus;")
    auth = d.vertex("认证账号", 585, 186, 120, 46, NODE + "rounded=0;")
    ok = d.vertex("认证通过？", 755, 174, 112, 70, NODE + "rhombus;")
    end = d.vertex("进入系统", 925, 190, 100, 38, NODE + "rounded=1;arcSize=50;")
    err1 = d.vertex("提示格式错误", 395, 300, 140, 42, NODE + "rounded=0;")
    err2 = d.vertex("提示认证失败", 732, 300, 140, 42, NODE + "rounded=0;")
    for a, b, label in [(start, input_, ""), (input_, check, ""), (check, auth, "是"), (check, err1, "否"), (auth, ok, ""), (ok, end, "是"), (ok, err2, "否")]:
        d.edge(a, b, label)
    d.write(path)


def gateway_agent_flow(path: Path) -> None:
    d = Diagram("Agent执行流程", width=1360, height=500)
    d.title("AI Agent Gateway 执行闭环流程", x=520, y=24, w=320)

    start_style = COMPACT_NODE + "rounded=1;arcSize=50;"
    process_style = COMPACT_NODE + "rounded=0;"
    subprocess_style = COMPACT_NODE + "rounded=0;"
    decision_style = COMPACT_NODE + "rhombus;"
    flow = EDGE + "endArrow=block;"

    start = d.vertex("开始", 45, 145, 72, 34, start_style)
    entry = d.vertex("任务入口", 150, 137, 82, 50, process_style)
    task_queue = d.vertex("任务队列", 265, 137, 90, 50, process_style)
    router = d.vertex("消息路由", 395, 137, 90, 50, process_style)
    session = d.vertex("组装上下文", 525, 137, 105, 50, subprocess_style)
    model = d.vertex("调用模型", 675, 137, 90, 50, process_style)
    need_tool = d.vertex("需要\n工具?", 805, 127, 82, 70, decision_style)
    reply = d.vertex("生成回复", 925, 137, 90, 50, process_style)
    delivery = d.vertex("投递队列", 1045, 137, 90, 50, process_style)
    outbound = d.vertex("通道发送", 1165, 137, 90, 50, process_style)
    end = d.vertex("结束", 1285, 145, 72, 34, start_style)

    tool = d.vertex("执行工具", 805, 270, 90, 50, process_style)
    tool_result = d.vertex("回灌结果", 675, 270, 90, 50, process_style)

    d.edge(start, entry, "", flow)
    d.edge(entry, task_queue, "", flow)
    d.edge(task_queue, router, "", flow)
    d.edge(router, session, "", flow)
    d.edge(session, model, "", flow)
    d.edge(model, need_tool, "", flow)
    d.edge(need_tool, reply, "否", flow)
    d.edge(reply, delivery, "", flow)
    d.edge(delivery, outbound, "", flow)
    d.edge(outbound, end, "", flow)

    d.edge(need_tool, tool, "是", flow)
    d.edge(tool, tool_result, "", flow)
    d.edge(tool_result, model, "", flow)

    d.write(path)


def class_diagram(path: Path) -> None:
    d = Diagram("class")
    d.title("类图：订单模型")
    service = d.vertex("OrderService\n----\n- repository: OrderRepository\n----\n+ createOrder(cmd)\n+ cancelOrder(id)", 120, 150, 250, 170, NODE + "swimlane;startSize=26;")
    order = d.vertex("Order\n----\n- id: UUID\n- status: OrderStatus\n----\n+ pay()\n+ cancel()", 470, 150, 230, 170, NODE + "swimlane;startSize=26;")
    item = d.vertex("OrderItem\n----\n- productId: UUID\n- quantity: int\n----\n+ subtotal(): Money", 470, 440, 230, 150, NODE + "swimlane;startSize=26;")
    repo = d.vertex("<<interface>>\nOrderRepository\n----\n+ save(order)\n+ findById(id)", 800, 150, 230, 140, NODE + "swimlane;startSize=42;")
    d.edge(service, order, "uses", EDGE + "endArrow=open;dashed=1;")
    d.edge(service, repo, "depends on", EDGE + "endArrow=open;dashed=1;")
    d.edge(order, item, "1..*", EDGE + "endArrow=diamondThin;endFill=1;")
    d.write(path)


def sequence_diagram(path: Path) -> None:
    d = Diagram("sequence")
    d.title("时序图：登录验证", x=485, y=35, w=200)
    header = NODE + "rounded=0;"
    d.vertex("用户", 95, 95, 96, 36, header)
    d.vertex("登录页面", 345, 95, 108, 36, header)
    d.vertex("认证服务", 610, 95, 108, 36, header)
    d.vertex("用户数据库", 870, 95, 120, 36, header)
    lifeline = EDGE + "endArrow=none;dashed=1;"
    for x in (143, 399, 664, 930):
        d.point_edge("", x, 131, x, 545, lifeline)
    active = "rounded=0;html=1;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1;"
    d.vertex("", 393, 165, 12, 315, active)
    d.vertex("", 658, 215, 220, 255, active)
    d.vertex("", 924, 280, 12, 60, active)
    d.vertex("说明：认证服务内部完成输入格式校验与密码摘要比对", 520, 250, 250, 34, TEXT + "align=left;fontSize=10;")
    d.vertex("alt", 72, 350, 940, 150, NODE + "rounded=0;fillColor=none;align=left;verticalAlign=top;")
    d.vertex("[认证通过]", 88, 372, 120, 24, TEXT + "align=left;")
    d.vertex("[认证失败]", 88, 438, 120, 24, TEXT + "align=left;")
    d.point_edge("", 72, 425, 1012, 425, EDGE + "endArrow=none;dashed=1;")
    messages = [
        ("输入账号和密码", 143, 170, 393, 170, False),
        ("提交登录请求", 405, 220, 658, 220, False),
        ("查询用户记录", 670, 305, 924, 305, False),
        ("返回用户摘要", 924, 340, 670, 340, True),
        ("返回令牌", 658, 395, 405, 395, True),
        ("进入系统", 393, 415, 143, 415, True),
        ("返回错误原因", 658, 465, 405, 465, True),
        ("提示登录失败", 393, 485, 143, 485, True),
    ]
    for label, x1, y1, x2, y2, dashed in messages:
        style = EDGE + ("endArrow=open;dashed=1;" if dashed else "endArrow=block;")
        d.point_edge(label, x1, y1, x2, y2, style)
    d.write(path)


def usecase_diagram(path: Path) -> None:
    d = Diagram("usecase")
    d.title("用例图：登录认证", x=485, y=35, w=200)
    boundary = d.vertex("登录认证系统", 285, 110, 610, 330, NODE + "rounded=0;fillColor=none;align=left;verticalAlign=top;spacing=8;")
    actor = "shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;fillColor=#ffffff;strokeColor=#000000;strokeWidth=1;" + FONT
    user = d.vertex("用户", 105, 195, 55, 85, actor)
    admin = d.vertex("管理员", 105, 310, 55, 85, actor)
    sms = d.vertex("短信服务", 1010, 205, 60, 90, actor)
    uc_login = d.vertex("登录系统", 390, 165, 150, 56, NODE + "ellipse;")
    uc_validate = d.vertex("校验凭证", 620, 165, 150, 56, NODE + "ellipse;")
    uc_reset = d.vertex("重置密码", 390, 290, 150, 56, NODE + "ellipse;")
    uc_code = d.vertex("发送验证码", 620, 290, 150, 56, NODE + "ellipse;")
    uc_lock = d.vertex("锁定账号", 390, 380, 150, 56, NODE + "ellipse;")
    assoc = EDGE + "endArrow=none;"
    dep = EDGE + "endArrow=open;dashed=1;"
    d.edge(user, uc_login, "", assoc)
    d.edge(user, uc_reset, "", assoc)
    d.edge(admin, uc_lock, "", assoc)
    d.edge(uc_login, uc_validate, "<<include>>", dep)
    d.edge(uc_reset, uc_code, "<<include>>", dep)
    d.edge(uc_code, sms, "", assoc)
    d.write(path)


def activity_diagram(path: Path) -> None:
    d = Diagram("activity")
    d.title("活动图：登录认证", x=485, y=35, w=200)
    action = NODE + "rounded=1;arcSize=14;"
    decision = NODE + "rhombus;"
    initial = "ellipse;html=1;shape=startState;fillColor=#000000;strokeColor=#000000;strokeWidth=1;"
    final = "ellipse;html=1;shape=endState;fillColor=#000000;strokeColor=#000000;strokeWidth=1;"
    start = d.vertex("", 70, 252, 24, 24, initial)
    input_ = d.vertex("输入账号和密码", 130, 240, 130, 46, action)
    check_input = d.vertex("输入合法？", 305, 228, 100, 70, decision)
    query_user = d.vertex("查询用户", 450, 240, 105, 46, action)
    user_exists = d.vertex("用户存在？", 600, 228, 100, 70, decision)
    compare_password = d.vertex("比对密码", 745, 240, 105, 46, action)
    password_ok = d.vertex("密码正确？", 895, 228, 100, 70, decision)
    create_session = d.vertex("创建会话", 1030, 240, 105, 46, action)
    success = d.vertex("返回登录成功", 1015, 350, 135, 46, action)
    format_error = d.vertex("提示格式错误", 285, 360, 130, 46, action)
    user_error = d.vertex("提示账号不存在", 580, 360, 140, 46, action)
    password_error = d.vertex("提示密码错误", 875, 360, 130, 46, action)
    end = d.vertex("", 592, 505, 30, 30, final)
    flow = EDGE + "endArrow=block;"
    for a, b, label in [
        (start, input_, ""),
        (input_, check_input, ""),
        (check_input, query_user, "[是]"),
        (query_user, user_exists, ""),
        (user_exists, compare_password, "[是]"),
        (compare_password, password_ok, ""),
        (password_ok, create_session, "[是]"),
        (create_session, success, ""),
    ]:
        d.edge(a, b, label, flow)
    d.routed_edge(check_input, format_error, "[否]", [(355, 330)], flow)
    d.routed_edge(user_exists, user_error, "[否]", [(650, 330)], flow)
    d.routed_edge(password_ok, password_error, "[否]", [(945, 330)], flow)
    d.routed_edge(format_error, end, "", [(350, 520)], flow)
    d.routed_edge(user_error, end, "", [(650, 520)], flow)
    d.routed_edge(password_error, end, "", [(940, 520)], flow)
    d.routed_edge(success, end, "", [(1082, 520)], flow)
    d.write(path)


def er_diagram(path: Path) -> None:
    d = Diagram("er")
    d.title("ER 图：订单数据模型")
    entity_style = NODE + "swimlane;startSize=28;"
    user = d.vertex("User\n----\nPK id\nname\nemail", 100, 150, 200, 130, entity_style)
    order = d.vertex("Order\n----\nPK id\nFK user_id\nstatus\ntotal_amount", 450, 140, 220, 150, entity_style)
    item = d.vertex("OrderItem\n----\nPK id\nFK order_id\nFK product_id\nquantity", 450, 430, 220, 150, entity_style)
    product = d.vertex("Product\n----\nPK id\nname\nsku\nprice", 800, 430, 200, 140, entity_style)
    payment = d.vertex("Payment\n----\nPK id\nFK order_id\nprovider\nstatus", 800, 140, 200, 140, entity_style)
    d.edge(user, order, "1 : N", EDGE + "endArrow=ERmany;startArrow=ERone;")
    d.edge(order, item, "1 : N", EDGE + "endArrow=ERmany;startArrow=ERone;")
    d.edge(product, item, "1 : N", EDGE + "endArrow=ERmany;startArrow=ERone;")
    d.edge(order, payment, "1 : 0..1", EDGE + "endArrow=ERzeroToOne;startArrow=ERone;")
    d.write(path)


def state_diagram(path: Path) -> None:
    d = Diagram("state")
    d.title("状态图：订单生命周期", x=465, y=35, w=240)
    start = d.vertex("", 90, 205, 24, 24, "ellipse;html=1;shape=startState;fillColor=#000000;strokeColor=#000000;")
    created = d.vertex("待支付", 165, 185, 105, 58, NODE + "rounded=1;arcSize=12;")
    paid = d.vertex("已支付", 375, 185, 105, 58, NODE + "rounded=1;arcSize=12;")
    shipped = d.vertex("已发货", 585, 185, 105, 58, NODE + "rounded=1;arcSize=12;")
    done = d.vertex("已完成", 795, 185, 105, 58, NODE + "rounded=1;arcSize=12;")
    canceled = d.vertex("已取消", 375, 280, 105, 58, NODE + "rounded=1;arcSize=12;")
    end = d.vertex("", 1020, 200, 28, 28, "ellipse;html=1;shape=endState;fillColor=#000000;strokeColor=#000000;")
    for a, b, label in [(start, created, "create"), (created, paid, "paySuccess"), (paid, shipped, "ship"), (shipped, done, "confirm"), (created, canceled, "timeout/cancel"), (paid, canceled, "refund"), (done, end, "close"), (canceled, end, "close")]:
        d.edge(a, b, label)
    d.write(path)


def architecture_diagram(path: Path) -> None:
    d = Diagram("architecture")
    d.title("架构图：电商系统容器视图")
    boundary = d.vertex("电商系统边界", 330, 120, 670, 440, NODE + "rounded=0;dashed=1;align=left;verticalAlign=top;spacing=10;")
    client = d.vertex("用户\n[Person]", 80, 240, 150, 70, NODE + "rounded=1;arcSize=8;")
    gateway = d.vertex("API Gateway\n[Container]\n认证、限流、路由", 400, 180, 180, 90, NODE + "rounded=1;arcSize=8;")
    web = d.vertex("Web 应用\n[Container]\n前端页面", 400, 360, 180, 90, NODE + "rounded=1;arcSize=8;")
    service = d.vertex("订单服务\n[Container]\n订单业务逻辑", 700, 180, 180, 90, NODE + "rounded=1;arcSize=8;")
    db = d.vertex("订单数据库\n[Container]\n存储订单数据", 700, 360, 180, 90, NODE + "shape=cylinder3d;boundedLbl=1;backgroundOutline=1;size=15;")
    mq = d.vertex("消息队列\n[External System]\n异步事件", 1060, 260, 160, 80, NODE + "rounded=1;arcSize=8;")
    for a, b, label in [(client, web, "访问 HTTPS"), (web, gateway, "调用 API"), (gateway, service, "转发请求"), (service, db, "读写数据"), (service, mq, "发布事件")]:
        d.edge(a, b, label, EDGE + "endArrow=block;")
    d.write(path)


def deployment_diagram(path: Path) -> None:
    d = Diagram("deployment")
    d.title("部署图：生产环境")
    internet = d.vertex("Internet 用户", 80, 180, 150, 70, NODE + "shape=cloud;")
    lb = d.vertex("负载均衡\n<<node>>", 330, 180, 170, 70, NODE + "rounded=1;arcSize=8;")
    cluster = d.vertex("Kubernetes 集群\n<<node>>", 160, 340, 650, 320, NODE + "rounded=0;align=left;verticalAlign=top;spacing=10;")
    ingress = d.vertex("Ingress\n<<executionEnvironment>>", 380, 390, 190, 60, NODE + "rounded=1;arcSize=8;")
    pod1 = d.vertex("web pod x2\n<<artifact>>", 220, 520, 160, 70, NODE + "rounded=1;arcSize=8;")
    pod2 = d.vertex("order-service pod x2\n<<artifact>>", 470, 520, 190, 70, NODE + "rounded=1;arcSize=8;")
    mysql = d.vertex("Managed MySQL\n<<node>>", 930, 420, 180, 80, NODE + "shape=cylinder3d;boundedLbl=1;backgroundOutline=1;size=15;")
    redis = d.vertex("Managed Redis\n<<node>>", 930, 560, 180, 80, NODE + "shape=cylinder3d;boundedLbl=1;backgroundOutline=1;size=15;")
    for a, b, label in [(internet, lb, "HTTPS"), (lb, ingress, "HTTPS"), (ingress, pod1, "路由"), (ingress, pod2, "REST"), (pod2, mysql, "MySQL"), (pod2, redis, "Redis")]:
        d.edge(a, b, label, EDGE + "endArrow=block;")
    d.write(path)


TEMPLATES = {
    "activity": activity_diagram,
    "architecture": architecture_diagram,
    "flowchart": flowchart,
    "gateway-agent-flow": gateway_agent_flow,
    "class": class_diagram,
    "deployment": deployment_diagram,
    "er": er_diagram,
    "sequence": sequence_diagram,
    "state": state_diagram,
    "usecase": usecase_diagram,
}


def validate(path: Path) -> None:
    ET.parse(path)
    text = path.read_text(encoding="utf-8")
    if "#dae8fc" in text or "#d5e8d4" in text or "#f8cecc" in text:
        raise SystemExit("colored fills are not allowed")
    if "fontFamily=SimSun" not in text:
        raise SystemExit("SimSun font missing")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("diagram_type", choices=sorted(TEMPLATES))
    parser.add_argument("output")
    args = parser.parse_args()
    output = resolve_output(args.output)
    TEMPLATES[args.diagram_type](output)
    validate(output)
    print(output.relative_to(repo_root()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
