"use strict";

const catalog = JSON.parse(document.getElementById("catalog-data").textContent);
const form = document.getElementById("forecast-form");
const routeInput = document.getElementById("route");
const dateInput = document.getElementById("date");
const priceInput = document.getElementById("price");
const button = document.getElementById("calculate");
const errorBox = document.getElementById("form-error");
const context = document.getElementById("result-context");
const canvas = document.getElementById("demand-chart");
const empty = document.getElementById("chart-empty");
const details = document.getElementById("scenario-details");
const number = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 1});
const money = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 2});
let chart = null;
let activeRequest = null;
let version = 0;

function selectedRoute() {
  return catalog.routes.find(route => route.route === routeInput.value);
}

function clearResult() {
  version += 1;
  if (activeRequest) activeRequest.abort();
  activeRequest = null;
  button.disabled = false;
  button.textContent = "Рассчитать →";
  errorBox.hidden = true;
  for (const name of ["seats", "demand", "sold"]) {
    document.getElementById(`${name}-value`).textContent = "—";
  }
  document.getElementById("free-seats").textContent = "С учётом количества мест";
  document.getElementById("result-note").hidden = true;
  context.textContent = "Результат появится после расчёта";
  if (chart) chart.destroy();
  chart = null;
  canvas.hidden = true;
  empty.hidden = false;
  details.hidden = true;
}

function routeChanged() {
  const route = selectedRoute();
  priceInput.value = route.base_price;
  dateInput.min = route.dates[0];
  dateInput.max = route.dates[route.dates.length - 1];
  if (!route.dates.includes(dateInput.value)) dateInput.value = route.dates[0];
  document.getElementById("base-price").textContent = `Базовая цена: ${money.format(route.base_price)} ₽`;
  clearResult();
}

const capacityMarker = {
  id: "capacityMarker",
  afterDraw(instance, args, options) {
    const {ctx, chartArea, scales} = instance;
    const x = scales.x.getPixelForValue(options.seats);
    if (x < chartArea.left || x > chartArea.right) return;
    ctx.save();
    ctx.strokeStyle = "#8b9ab0";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#7b8ba2";
    ctx.font = "11px Manrope, Arial, sans-serif";
    ctx.textAlign = x > chartArea.right - 100 ? "right" : "left";
    ctx.fillText(`${options.seats} мест`, x + (ctx.textAlign === "right" ? -6 : 6), chartArea.top + 12);
    ctx.restore();
  }
};

function drawChart(data) {
  if (chart) chart.destroy();
  empty.hidden = true;
  canvas.hidden = false;
  if (typeof Chart === "undefined") {
    canvas.hidden = true;
    empty.hidden = false;
    empty.querySelector("strong").textContent = "График не загрузился";
    empty.querySelector("p").textContent = "Все сценарии доступны в таблице ниже.";
    return;
  }
  const points = data.scenarios.map(item => ({x: item.demand, y: item.quantile}));
  const values = [...points.map(item => item.x), data.seats];
  const range = Math.max(Math.max(...values) - Math.min(...values), 100);
  chart = new Chart(canvas, {
    type: "scatter",
    plugins: [capacityMarker],
    data: {
      datasets: [
        {label: "Сценарии спроса", data: points, showLine: true, tension: 0,
          borderColor: "#315ce8", backgroundColor: "#fff", pointBorderColor: "#315ce8",
          borderWidth: 2.5, pointBorderWidth: 2, pointRadius: 4, pointHoverRadius: 6},
        {label: "Медиана", data: [points[4]], backgroundColor: "#315ce8",
          borderColor: "#fff", borderWidth: 3, pointRadius: 8, pointHoverRadius: 9}
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : {duration: 250},
      font: {family: "Manrope, Arial, sans-serif"},
      layout: {padding: {top: 10, right: 10}},
      scales: {
        x: {type: "linear", min: Math.min(0, Math.min(...values) - range * .07),
          max: Math.max(...values) + range * .14,
          title: {display: true, text: "Расчётный спрос, пассажиры", color: "#8898ad", font: {size: 10}},
          grid: {color: "#eef2f7"}, border: {display: false},
          ticks: {color: "#8a9ab0", font: {size: 10}, maxTicksLimit: 7,
            callback: value => number.format(value)}},
        y: {min: 0, max: 1,
          title: {display: true, text: "Уровень квантиля", color: "#8898ad", font: {size: 10}},
          grid: {color: "#eef2f7"}, border: {display: false},
          ticks: {stepSize: .1, color: "#8a9ab0", font: {size: 10}, callback: value => number.format(value)}}
      },
      plugins: {
        legend: {display: false},
        capacityMarker: {seats: data.seats},
        tooltip: {backgroundColor: "#1e3152", padding: 12, displayColors: false,
          callbacks: {
            title: items => `Квантиль ${number.format(items[0].parsed.y)}`,
            label: item => `Спрос: ${number.format(item.parsed.x)} чел.`
          }}
      }
    }
  });
  canvas.setAttribute("aria-label", `Сценарии спроса. Медиана: ${number.format(data.demand)} пассажиров. Вместимость: ${data.seats} мест. Числа доступны в таблице ниже.`);
}

function showResult(data) {
  document.getElementById("seats-value").textContent = number.format(data.seats);
  document.getElementById("demand-value").textContent = number.format(data.demand);
  document.getElementById("sold-value").textContent = number.format(data.sold);
  document.getElementById("free-seats").textContent = `Свободно по прогнозу: ${number.format(data.free_seats)} мест`;
  const date = new Date(`${data.date}T12:00:00`);
  context.textContent = `Москва → ${data.destination} · ${date.toLocaleDateString("ru-RU", {day: "numeric", month: "long"})} · ${money.format(data.price)} ₽`;
  const note = document.getElementById("result-note");
  note.hidden = false;
  note.className = "result-note";
  if (data.demand < 0) {
    note.textContent = "При такой цене линейная модель даёт отрицательный спрос. Покупателей и продаж — 0; на графике сохранены исходные значения модели.";
    note.classList.add("warning");
  } else if (data.demand > data.seats) {
    note.textContent = `По медианному сценарию желающих больше, чем мест, на ${number.format(data.demand - data.seats)} чел. Продажи ограничены вместимостью.`;
  } else {
    note.textContent = "По медианному сценарию мест хватает. Прогноз продаж не означает, что билеты уже забронированы.";
  }
  const body = document.getElementById("scenario-table");
  body.replaceChildren();
  for (const item of data.scenarios) {
    const row = document.createElement("tr");
    const quantile = document.createElement("td");
    quantile.textContent = number.format(item.quantile);
    const demand = document.createElement("td");
    demand.textContent = number.format(item.demand);
    row.append(quantile, demand);
    body.append(row);
  }
  details.hidden = false;
  drawChart(data);
}

routeInput.addEventListener("change", routeChanged);
for (const input of [dateInput, priceInput]) input.addEventListener("input", clearResult);
form.addEventListener("submit", async event => {
  event.preventDefault();
  errorBox.hidden = true;
  if (!form.reportValidity()) return;
  const route = selectedRoute();
  if (!route || !route.dates.includes(dateInput.value)) {
    errorBox.textContent = "Для этой даты прогноза нет. Выберите дату из доступного периода.";
    errorBox.hidden = false;
    return;
  }
  if (activeRequest) activeRequest.abort();
  const controller = new AbortController();
  activeRequest = controller;
  const currentVersion = ++version;
  button.disabled = true;
  button.textContent = "Рассчитываем…";
  try {
    const response = await fetch(form.dataset.endpoint, {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: {"Content-Type": "application/json", "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content},
      body: JSON.stringify({origin: "MOW", route: routeInput.value, date: dateInput.value, price: priceInput.value})
    });
    const data = await response.json();
    if (currentVersion !== version) return;
    if (!response.ok) {
      if (response.status === 401 && data.login_url) {
        window.location.assign(data.login_url);
        return;
      }
      throw new Error(data.error || "Не удалось выполнить расчёт. Попробуйте ещё раз.");
    }
    showResult(data);
  } catch (error) {
    if (error.name === "AbortError" || currentVersion !== version) return;
    errorBox.textContent = error instanceof TypeError ? "Нет соединения с сервером. Проверьте подключение и повторите расчёт." : error.message;
    errorBox.hidden = false;
  } finally {
    if (currentVersion === version) {
      button.disabled = false;
      button.textContent = "Рассчитать →";
      activeRequest = null;
    }
  }
});
