/* =============================================================
   网约车系统 —— 前端脚本
   纯原生 JS，不依赖任何框架或外部服务（含地图 API）
   ============================================================= */
(function () {
    'use strict';

    /* ---------------------------------------------------------
       1. 订单状态自动刷新
          只刷新「进行中」的订单详情页，让乘客能看到状态流转。
          用整页刷新而不是 AJAX，是为了让服务端渲染的模板
          （时间线、徽章）保持唯一数据来源，不做两套渲染逻辑。
       --------------------------------------------------------- */
    function setupAutoRefresh() {
        var holder = document.querySelector('[data-autorefresh]');
        if (!holder) return;

        var interval = parseInt(holder.getAttribute('data-autorefresh'), 10) || 15000;
        var status = holder.getAttribute('data-status') || '';
        var watch = ['pending', 'accepted', 'arrived', 'in_progress'];
        if (watch.indexOf(status) === -1) return;

        setTimeout(function () {
            window.location.reload();
        }, interval);
    }

    /* ---------------------------------------------------------
       2. 危险操作二次确认
          用 data-confirm 属性声明提示语
       --------------------------------------------------------- */
    function setupConfirm() {
        document.addEventListener('submit', function (e) {
            var form = e.target;
            if (!form.hasAttribute || !form.hasAttribute('data-confirm')) return;
            var msg = form.getAttribute('data-confirm') || '确定要执行该操作吗？';
            if (!window.confirm(msg)) {
                e.preventDefault();
            }
        });
    }

    /* ---------------------------------------------------------
       3. 轨迹回放：Canvas 自绘
          【为什么不用高德/百度地图】
            地图 API 需要申请密钥、依赖外网，演示或答辩现场
            一旦断网就整块空白。这里改成自己把经纬度投影到画布上，
            画折线 + 起终点标记 + 方向箭头，零外部依赖，效果够用。

          投影方式：把经纬度按范围线性映射到画布内边距框，
          纬度方向需要翻转（纬度越大越靠北，而画布 y 轴向下）。
       --------------------------------------------------------- */
    function setupTrackCanvas() {
        var canvas = document.getElementById('track-canvas');
        if (!canvas) return;

        var raw = canvas.getAttribute('data-points');
        if (!raw) return;

        var points;
        try {
            points = JSON.parse(raw);
        } catch (err) {
            return;
        }
        if (!points || points.length < 2) return;

        // 高分屏适配
        var dpr = window.devicePixelRatio || 1;
        var rect = canvas.getBoundingClientRect();
        var W = rect.width || canvas.clientWidth || 600;
        var H = parseFloat(canvas.getAttribute('height')) || 360;

        canvas.width = W * dpr;
        canvas.height = H * dpr;
        canvas.style.height = H + 'px';

        var ctx = canvas.getContext('2d');
        ctx.scale(dpr, dpr);
        ctx.clearRect(0, 0, W, H);

        // ---- 计算经纬度范围 ----
        var lngs = points.map(function (p) { return p.lng; });
        var lats = points.map(function (p) { return p.lat; });
        var minLng = Math.min.apply(null, lngs), maxLng = Math.max.apply(null, lngs);
        var minLat = Math.min.apply(null, lats), maxLat = Math.max.apply(null, lats);

        // 避免所有点重合导致除以 0
        var spanLng = (maxLng - minLng) || 0.001;
        var spanLat = (maxLat - minLat) || 0.001;

        var pad = 44;
        var plotW = W - pad * 2;
        var plotH = H - pad * 2;

        // 保持横纵等比，避免轨迹被拉伸变形
        var scale = Math.min(plotW / spanLng, plotH / spanLat);
        var drawW = spanLng * scale;
        var drawH = spanLat * scale;
        var offsetX = pad + (plotW - drawW) / 2;
        var offsetY = pad + (plotH - drawH) / 2;

        function project(p) {
            return {
                x: offsetX + (p.lng - minLng) * scale,
                // 纬度越大越靠北 -> 画布 y 越小
                y: offsetY + (maxLat - p.lat) * scale
            };
        }

        var pts = points.map(function (p) {
            var xy = project(p);
            xy.speed = p.speed;
            xy.t = p.t;
            return xy;
        });

        // ---- 背景网格 ----
        ctx.strokeStyle = '#e8edf3';
        ctx.lineWidth = 1;
        for (var gx = 0; gx <= 4; gx++) {
            var x = pad + plotW * gx / 4;
            ctx.beginPath(); ctx.moveTo(x, pad); ctx.lineTo(x, pad + plotH); ctx.stroke();
        }
        for (var gy = 0; gy <= 3; gy++) {
            var y = pad + plotH * gy / 3;
            ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(pad + plotW, y); ctx.stroke();
        }

        // ---- 轨迹折线：按速度着色 ----
        // 低速偏蓝、高速偏红，直观反映行驶状态
        for (var i = 1; i < pts.length; i++) {
            var a = pts[i - 1], b = pts[i];
            var sp = (b.speed == null ? a.speed : b.speed) || 0;
            var ratio = Math.max(0, Math.min(1, sp / 70));
            var r = Math.round(37 + ratio * 200);
            var g = Math.round(99 + (1 - ratio) * 80);
            var bl = Math.round(235 - ratio * 200);

            ctx.strokeStyle = 'rgba(' + r + ',' + g + ',' + bl + ',0.9)';
            ctx.lineWidth = 3;
            ctx.lineCap = 'round';
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
        }

        // ---- 采样点 ----
        ctx.fillStyle = 'rgba(37,99,235,0.5)';
        var step = Math.max(1, Math.floor(pts.length / 40));
        for (var k = 0; k < pts.length; k += step) {
            ctx.beginPath();
            ctx.arc(pts[k].x, pts[k].y, 2, 0, Math.PI * 2);
            ctx.fill();
        }

        // ---- 起点 / 终点标记 ----
        function marker(pt, color, label) {
            ctx.beginPath();
            ctx.arc(pt.x, pt.y, 8, 0, Math.PI * 2);
            ctx.fillStyle = color;
            ctx.fill();
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = 2.5;
            ctx.stroke();

            ctx.fillStyle = color;
            ctx.font = 'bold 12px "Microsoft YaHei", sans-serif';
            ctx.textAlign = 'center';
            ctx.fillText(label, pt.x, pt.y - 14);
        }
        marker(pts[0], '#059669', '起点');
        marker(pts[pts.length - 1], '#dc2626', '终点');

        // ---- 图例 ----
        ctx.textAlign = 'left';
        ctx.font = '12px "Microsoft YaHei", sans-serif';
        ctx.fillStyle = '#6b7280';
        ctx.fillText('折线颜色随车速变化：蓝(慢) → 红(快)', pad, H - 14);
        ctx.textAlign = 'right';
        ctx.fillText('共 ' + pts.length + ' 个采样点', W - pad, H - 14);
    }

    /* ---------------------------------------------------------
       4. 表单：数字输入范围提示
          完成行程时里程/时长必须为正数
       --------------------------------------------------------- */
    function setupNumberGuards() {
        document.querySelectorAll('input[data-positive]').forEach(function (el) {
            el.addEventListener('blur', function () {
                var v = parseFloat(el.value);
                if (isNaN(v) || v <= 0) {
                    el.setAttribute('data-invalid', '1');
                } else {
                    el.removeAttribute('data-invalid');
                }
            });
        });
    }

    /* ---------------------------------------------------------
       5. 常用地址选择：选中后自动填充上车点
       --------------------------------------------------------- */
    function setupAddressPick() {
        var sel = document.getElementById('address-select');
        if (!sel) return;
        sel.addEventListener('change', function () {
            var opt = sel.options[sel.selectedIndex];
            if (!opt || !opt.value) return;
            var nameInput = document.getElementById('origin_address');
            if (nameInput) nameInput.value = opt.getAttribute('data-name') || '';
        });
    }

    /* ---------------------------------------------------------
       6. 统计页纯 CSS 柱状图的高度计算
          （模板只输出数值，高度在这里算，避免在模板里做算术）
       --------------------------------------------------------- */
    function setupBarHeights() {
        document.querySelectorAll('[data-bar-chart]').forEach(function (chart) {
            var max = parseFloat(chart.getAttribute('data-max')) || 0;
            if (max <= 0) {
                chart.querySelectorAll('.chart-bar').forEach(function (bar) {
                    var v = parseFloat(bar.getAttribute('data-value')) || 0;
                    bar.style.height = v > 0 ? '4px' : '0';
                });
                return;
            }
            chart.querySelectorAll('.chart-bar').forEach(function (bar) {
                var v = parseFloat(bar.getAttribute('data-value')) || 0;
                var pct = Math.max(v > 0 ? 2 : 0, (v / max) * 100);
                bar.style.height = pct + '%';
                bar.title = bar.getAttribute('data-label') + '：' + v;
            });
        });
    }

    /* ---------------------------------------------------------
       7. 表格内快速筛选（纯前端，不重新请求）
       --------------------------------------------------------- */
    function setupTableFilter() {
        document.querySelectorAll('[data-table-filter]').forEach(function (input) {
            var tableId = input.getAttribute('data-table-filter');
            var table = document.getElementById(tableId);
            if (!table) return;
            input.addEventListener('input', function () {
                var kw = input.value.trim().toLowerCase();
                table.querySelectorAll('tbody tr').forEach(function (tr) {
                    tr.style.display = (!kw || tr.textContent.toLowerCase().indexOf(kw) !== -1)
                        ? '' : 'none';
                });
            });
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        setupAutoRefresh();
        setupConfirm();
        setupTrackCanvas();
        setupNumberGuards();
        setupAddressPick();
        setupBarHeights();
        setupTableFilter();
    });
})();
