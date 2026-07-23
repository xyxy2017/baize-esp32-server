import asyncio
import time
import uuid
from aiohttp import web
from config.logger import setup_logging
from core.api.app_demo_store import admin_metrics
from core.api.app_demo_handler import AppDemoHandler
from core.api.health_handler import HealthHandler
from core.api.ota_handler import OTAHandler
from core.api.vision_handler import VisionHandler
from core.telemetry import metrics_payload, observe_http_request, set_business_snapshot

TAG = __name__


class SimpleHttpServer:
    def __init__(self, config: dict, device_registry=None):
        self.config = config
        self.logger = setup_logging()
        self.app_demo_handler = AppDemoHandler(config, device_registry=device_registry)
        self.health_handler = HealthHandler(config)
        self.ota_handler = OTAHandler(config)
        self.vision_handler = VisionHandler(config)

    def _get_websocket_url(self, local_ip: str, port: int) -> str:
        """获取websocket地址

        Args:
            local_ip: 本地IP地址
            port: 端口号

        Returns:
            str: websocket地址
        """
        server_config = self.config["server"]
        websocket_config = server_config.get("websocket")

        if websocket_config and "你" not in websocket_config:
            return websocket_config
        else:
            return f"ws://{local_ip}:{port}/xiaozhi/v1/"

    async def start(self):
        try:
            server_config = self.config["server"]
            read_config_from_api = self.config.get("read_config_from_api", False)
            host = server_config.get("ip", "0.0.0.0")
            port = int(server_config.get("http_port", 8003))

            if port:
                @web.middleware
                async def telemetry_middleware(request, handler):
                    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
                    started = time.monotonic()
                    status = 500
                    try:
                        response = await handler(request)
                        status = response.status
                        response.headers["X-Request-ID"] = request_id
                        return response
                    except web.HTTPException as exc:
                        status = exc.status
                        raise
                    finally:
                        route = request.path
                        if request.match_info.route is not None:
                            resource = request.match_info.route.resource
                            if resource is not None:
                                route = resource.canonical
                        duration = time.monotonic() - started
                        observe_http_request(request.method, route, status, duration)
                        self.logger.bind(
                            tag=TAG,
                            request_id=request_id,
                            method=request.method,
                            route=route,
                            status=status,
                            duration_ms=round(duration * 1000, 2),
                        ).info("http_request")

                app = web.Application(middlewares=[telemetry_middleware])
                app.add_routes(self.app_demo_handler.routes())
                app.add_routes(self.health_handler.routes())
                app.add_routes([web.get("/metrics", self.handle_metrics)])

                if not read_config_from_api:
                    # 如果没有开启智控台，只是单模块运行，就需要再添加简单OTA接口，用于下发websocket接口
                    app.add_routes(
                        [
                            web.get("/xiaozhi/ota/", self.ota_handler.handle_get),
                            web.post("/xiaozhi/ota/", self.ota_handler.handle_post),
                            web.post(
                                "/xiaozhi/ota/activate",
                                self.ota_handler.handle_activate,
                            ),
                            web.options(
                                "/xiaozhi/ota/", self.ota_handler.handle_options
                            ),
                            # 下载接口，仅提供 data/bin/*.bin 下载
                            web.get(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_download,
                            ),
                            web.options(
                                "/xiaozhi/ota/download/{filename}",
                                self.ota_handler.handle_options,
                            ),
                            web.get(
                                "/xiaozhi/ota/assets/{filename}",
                                self.ota_handler.handle_asset_download,
                            ),
                            web.options(
                                "/xiaozhi/ota/assets/{filename}",
                                self.ota_handler.handle_options,
                            ),
                        ]
                    )
                # 添加路由
                app.add_routes(
                    [
                        web.get("/mcp/vision/explain", self.vision_handler.handle_get),
                        web.post(
                            "/mcp/vision/explain", self.vision_handler.handle_post
                        ),
                        web.options(
                            "/mcp/vision/explain", self.vision_handler.handle_options
                        ),
                    ]
                )

                # 运行服务
                runner = web.AppRunner(app)
                await runner.setup()
                site = web.TCPSite(runner, host, port)
                await site.start()

                # 保持服务运行
                while True:
                    await asyncio.sleep(3600)  # 每隔 1 小时检查一次
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"HTTP服务器启动失败: {e}")
            import traceback

            self.logger.bind(tag=TAG).error(f"错误堆栈: {traceback.format_exc()}")
            raise

    async def handle_metrics(self, request):
        remote = request.remote or ""
        if remote not in {"127.0.0.1", "::1"}:
            raise web.HTTPForbidden(text="metrics endpoint is local only")
        try:
            set_business_snapshot(admin_metrics(self.config))
        except Exception as exc:
            self.logger.bind(tag=TAG).warning(
                f"Unable to refresh metrics snapshot: {type(exc).__name__}"
            )
        body, content_type = metrics_payload()
        return web.Response(body=body, headers={"Content-Type": content_type})
