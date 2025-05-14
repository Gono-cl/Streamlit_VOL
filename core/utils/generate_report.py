import base64
import pandas as pd
from io import BytesIO
import altair as alt
import plotly.io as pio

def chart_to_base64(chart, chart_type='altair'):
    buf = BytesIO()
    if chart_type == 'altair':
        chart.save(buf, format='png')
    elif chart_type == 'plotly':
        img_bytes = pio.to_image(chart, format="png", width=1000, height=500, scale=2)
        buf.write(img_bytes)
    else:
        raise ValueError("Unsupported chart type")
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"

def generate_report(name, notes, date, df_results, best_result, progress_chart, parallel_chart):
    progress_img = chart_to_base64(progress_chart, chart_type='altair')
    parallel_img = chart_to_base64(parallel_chart, chart_type='plotly')

    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            h1 {{ color: #333; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
            th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            img {{ margin-top: 20px; max-width: 100%; height: auto; }}
        </style>
    </head>
    <body>
        <h1>Experiment Report</h1>
        <p><strong>Name:</strong> {name}</p>
        <p><strong>Date:</strong> {date}</p>
        <p><strong>Notes:</strong><br>{notes}</p>

        <h2>Best Result</h2>
        <table>
            {''.join(f'<tr><th>{k}</th><td>{v}</td></tr>' for k, v in best_result.items())}
        </table>

        <h2>All Results</h2>
        {df_results.to_html(index=False)}

        <h2>Progress Chart</h2>
        <img src="{progress_img}" />

        <h2>Parallel Coordinates</h2>
        <img src="{parallel_img}" />
    </body>
    </html>
    """
    return html_content.encode("utf-8")

