"""
Vistas del Sistema Contable.

Cada vista implementa una funcionalidad específica:
- index: Menú principal con resumen
- gestionar_cuentas: CRUD del catálogo de cuentas contables
- registrar_asiento: Formulario dinámico para crear asientos
- libro_diario: Reporte de todos los asientos por fecha
- libro_mayor: Movimientos agrupados por cuenta con saldos
- balance_comprobacion: Verificación de cuadre (Debe == Haber)
- estado_resultados: Cálculo de utilidad/pérdida
- balance_general: Activo = Pasivo + Patrimonio
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Sum
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.utils import timezone  # <-- Necesario para la hora real
from xhtml2pdf import pisa
from io import BytesIO
from .models import CuentaContable, AsientoContable, Movimiento
from .reporte_utils import get_reporte_context


# ─── PÁGINA PRINCIPAL ──────────────────────────────────────────────────────────

def index(request):
    """
    Muestra el menú principal con un resumen del estado del sistema.
    """
    total_asientos = AsientoContable.objects.count()
    total_cuentas = CuentaContable.objects.count()
    total_movimientos = Movimiento.objects.count()

    total_debe = Movimiento.objects.filter(tipo='debe').aggregate(
        total=Sum('monto'))['total'] or Decimal('0')
    total_haber = Movimiento.objects.filter(tipo='haber').aggregate(
        total=Sum('monto'))['total'] or Decimal('0')

    # Últimos 5 asientos por fecha de creación (los que acabas de hacer)
    ultimos_asientos = AsientoContable.objects.prefetch_related(
        'movimientos__cuenta'
    ).order_by('-created_at')[:5]

    context = {
        'total_asientos': total_asientos,
        'total_cuentas': total_cuentas,
        'total_movimientos': total_movimientos,
        'total_debe': total_debe,
        'total_haber': total_haber,
        'ultimos_asientos': ultimos_asientos,
    }
    return render(request, 'index.html', context)


# ─── GESTIÓN DE CUENTAS CONTABLES ──────────────────────────────────────────────

def gestionar_cuentas(request):
    if request.method == 'POST':
        codigo = request.POST.get('codigo', '').strip()
        nombre = request.POST.get('nombre', '').strip()
        tipo = request.POST.get('tipo', '').strip()
        subcategoria = request.POST.get('subcategoria', '').strip()

        errors = []
        if not codigo: errors.append('El código es obligatorio.')
        if not nombre: errors.append('El nombre es obligatorio.')
        if tipo not in dict(CuentaContable.TIPO_CHOICES): errors.append('El tipo de cuenta no es válido.')
        if CuentaContable.objects.filter(codigo=codigo).exists():
            errors.append(f'Ya existe una cuenta con el código "{codigo}".')

        if errors:
            for error in errors:
                messages.error(request, error)
        else:
            CuentaContable.objects.create(
                codigo=codigo, nombre=nombre, tipo=tipo, subcategoria=subcategoria
            )
            messages.success(request, f'Cuenta "{codigo} - {nombre}" creada exitosamente.')
            return redirect('gestionar_cuentas')

    cuentas = CuentaContable.objects.all()
    context = {
        'cuentas': cuentas,
        'tipos': CuentaContable.TIPO_CHOICES,
        'subcategorias': CuentaContable.SUBCATEGORIA_CHOICES,
    }
    return render(request, 'gestionar_cuentas.html', context)


def eliminar_cuenta(request, cuenta_id):
    cuenta = get_object_or_404(CuentaContable, id=cuenta_id)
    if cuenta.movimientos.exists():
        messages.error(request, f'No se puede eliminar "{cuenta}" porque tiene movimientos asociados.')
    else:
        messages.success(request, f'Cuenta "{cuenta}" eliminada exitosamente.')
        cuenta.delete()
    return redirect('gestionar_cuentas')


# ─── REGISTRO DE ASIENTOS CONTABLES ────────────────────────────────────────────

def registrar_asiento(request):
    cuentas = CuentaContable.objects.all()

    if not cuentas.exists():
        messages.warning(request, 'Debe crear al menos una cuenta contable antes de registrar asientos.')
        return redirect('gestionar_cuentas')

    if request.method == 'POST':
        fecha = request.POST.get('fecha', '').strip()
        descripcion = request.POST.get('descripcion', '').strip()
        num_movimientos = int(request.POST.get('num_movimientos', 0))

        errors = []
        if not fecha: errors.append('La fecha es obligatoria.')
        if num_movimientos < 2: errors.append('Debe registrar al menos 2 movimientos por asiento.')

        movimientos_data = []
        for i in range(num_movimientos):
            cuenta_id = request.POST.get(f'cuenta_{i}', '').strip()
            tipo = request.POST.get(f'tipo_{i}', '').strip()
            monto_str = request.POST.get(f'monto_{i}', '').strip()

            if not cuenta_id or not tipo or not monto_str:
                errors.append(f'Línea {i+1}: Todos los campos son obligatorios.')
                continue

            try:
                monto = Decimal(monto_str)
                if monto <= 0:
                    errors.append(f'Línea {i+1}: El monto debe ser mayor a 0.')
                    continue
            except (InvalidOperation, ValueError):
                errors.append(f'Línea {i+1}: El monto ingresado no es válido.')
                continue

            try:
                cuenta = CuentaContable.objects.get(id=int(cuenta_id))
            except (CuentaContable.DoesNotExist, ValueError):
                errors.append(f'Línea {i+1}: La cuenta seleccionada no existe.')
                continue

            movimientos_data.append({'cuenta': cuenta, 'tipo': tipo, 'monto': monto})

        if not errors and len(movimientos_data) >= 2:
            total_debe = sum(m['monto'] for m in movimientos_data if m['tipo'] == 'debe')
            total_haber = sum(m['monto'] for m in movimientos_data if m['tipo'] == 'haber')

            if total_debe != total_haber:
                errors.append(f'El asiento no está balanceado. Debe: S/ {total_debe:,.2f} ≠ Haber: S/ {total_haber:,.2f}')
        
        if errors:
            for error in errors: messages.error(request, error)
        else:
            asiento = AsientoContable.objects.create(fecha=fecha, descripcion=descripcion)
            for m in movimientos_data:
                Movimiento.objects.create(asiento=asiento, cuenta=m['cuenta'], tipo=m['tipo'], monto=m['monto'])
            
            # Fecha y hora actual para el mensaje
            ahora = timezone.localtime(timezone.now()).strftime("%d/%m/%Y a las %H:%M")
            messages.success(request, f'Asiento creado exitosamente el {ahora}.')
            return redirect('libro_diario')

    # Mostrar asientos registrados recientemente en la parte inferior del formulario
    asientos_registrados = AsientoContable.objects.prefetch_related('movimientos__cuenta').order_by('-created_at')

    context = {
        'cuentas': cuentas,
        'asientos_registrados': asientos_registrados,
    }
    return render(request, 'registrar_asiento.html', context)


# ─── LIBRO DIARIO Y EDICIÓN ────────────────────────────────────────────────────

def libro_diario(request):
    """
    Muestra los asientos ordenados del más antiguo al más nuevo.
    """
    # IMPORTANTE: Orden ASCENDENTE para que el año 2000 sea el primer elemento
    asientos = AsientoContable.objects.prefetch_related('movimientos__cuenta').order_by('fecha', 'id')
    cuentas = CuentaContable.objects.all()
    
    context = {
        'asientos': asientos,
        'cuentas': cuentas,
    }
    return render(request, 'libro_diario.html', context)


def editar_asiento(request, asiento_id):
    """
    Procesa la edición del asiento desde el pop-up.
    """
    asiento = get_object_or_404(AsientoContable, id=asiento_id)
    if request.method == 'POST':
        # 1. Actualizamos cabecera
        asiento.fecha = request.POST.get('fecha')
        asiento.descripcion = request.POST.get('descripcion')
        asiento.save()
        
        # 2. Reemplazamos movimientos
        asiento.movimientos.all().delete()
        num_movs = int(request.POST.get('num_movimientos', 0))
        for i in range(num_movs):
            cuenta_id = request.POST.get(f'cuenta_{i}')
            tipo = request.POST.get(f'tipo_{i}')
            monto = request.POST.get(f'monto_{i}')
            if cuenta_id and monto:
                Movimiento.objects.create(
                    asiento=asiento,
                    cuenta_id=cuenta_id,
                    tipo=tipo,
                    monto=monto
                )
        
        # Fecha y hora actual para el mensaje
        ahora = timezone.localtime(timezone.now()).strftime("%d/%m/%Y a las %H:%M")
        messages.success(request, f"Asiento modificado exitosamente el {ahora}.")
        
    return redirect('libro_diario')


def eliminar_asiento(request, asiento_id):
    asiento = get_object_or_404(AsientoContable, id=asiento_id)
    asiento.delete()
    messages.success(request, f'El Asiento fue eliminado correctamente.')
    return redirect('libro_diario')


# ─── OTROS REPORTES (SIN CAMBIOS) ──────────────────────────────────────────────

def libro_mayor(request):
    cuentas = CuentaContable.objects.all()
    datos_cuentas = []
    for cuenta in cuentas:
        movimientos = cuenta.movimientos.select_related('asiento').order_by('asiento__fecha', 'asiento__id')
        if not movimientos.exists(): continue
        total_debe = movimientos.filter(tipo='debe').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        total_haber = movimientos.filter(tipo='haber').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        saldo = total_debe - total_haber if cuenta.naturaleza_deudora else total_haber - total_debe
        datos_cuentas.append({
            'cuenta': cuenta, 'movimientos': movimientos,
            'total_debe': total_debe, 'total_haber': total_haber, 'saldo': saldo,
        })
    return render(request, 'libro_mayor.html', {'datos_cuentas': datos_cuentas})


def balance_comprobacion(request):
    cuentas = CuentaContable.objects.all()
    datos = []
    gran_total_debe = gran_total_haber = gran_saldo_deudor = gran_saldo_acreedor = Decimal('0')
    for cuenta in cuentas:
        total_debe = cuenta.movimientos.filter(tipo='debe').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        total_haber = cuenta.movimientos.filter(tipo='haber').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        if total_debe == 0 and total_haber == 0: continue
        saldo = total_debe - total_haber
        saldo_deudor = saldo if saldo > 0 else Decimal('0')
        saldo_acreedor = abs(saldo) if saldo < 0 else Decimal('0')
        datos.append({'cuenta': cuenta, 'total_debe': total_debe, 'total_haber': total_haber, 'saldo_deudor': saldo_deudor, 'saldo_acreedor': saldo_acreedor})
        gran_total_debe += total_debe
        gran_total_haber += total_haber
        gran_saldo_deudor += saldo_deudor
        gran_saldo_acreedor += saldo_acreedor
    context = {
        'datos': datos, 'gran_total_debe': gran_total_debe, 'gran_total_haber': gran_total_haber,
        'gran_saldo_deudor': gran_saldo_deudor, 'gran_saldo_acreedor': gran_saldo_acreedor,
        'esta_cuadrado': gran_total_debe == gran_total_haber,
    }
    return render(request, 'balance_comprobacion.html', context)


def estado_resultados(request):
    def saldo_cuenta(cuenta):
        t_debe = cuenta.movimientos.filter(tipo='debe').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        t_haber = cuenta.movimientos.filter(tipo='haber').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        return t_debe - t_haber if cuenta.tipo == 'gasto' else t_haber - t_debe
    def obtener_items(queryset):
        items = []; total = Decimal('0')
        for c in queryset:
            s = saldo_cuenta(c)
            if s != 0: items.append({'cuenta': c, 'saldo': s}); total += s
        return items, total
    ventas, total_ventas = obtener_items(CuentaContable.objects.filter(tipo='ingreso').exclude(subcategoria='otro_ingreso'))
    costo_ventas, total_costo_ventas = obtener_items(CuentaContable.objects.filter(tipo='gasto', subcategoria='costo_ventas'))
    utilidad_bruta = total_ventas - total_costo_ventas
    gastos_operativos, total_gastos_operativos = obtener_items(CuentaContable.objects.filter(tipo='gasto').exclude(subcategoria__in=['costo_ventas', 'gasto_financiero', 'otro_gasto']))
    utilidad_operativa = utilidad_bruta - total_gastos_operativos
    gastos_financieros, total_gastos_financieros = obtener_items(CuentaContable.objects.filter(tipo='gasto', subcategoria='gasto_financiero'))
    otros_ingresos, total_otros_ingresos = obtener_items(CuentaContable.objects.filter(tipo='ingreso', subcategoria='otro_ingreso'))
    otros_gastos, total_otros_gastos = obtener_items(CuentaContable.objects.filter(tipo='gasto', subcategoria='otro_gasto'))
    utilidad_antes_impuesto = utilidad_operativa - total_gastos_financieros + total_otros_ingresos - total_otros_gastos
    impuesto = (utilidad_antes_impuesto * Decimal('0.30')).quantize(Decimal('0.01')) if utilidad_antes_impuesto > 0 else Decimal('0')
    context = {
        'er_total_ventas': total_ventas, 'er_total_costo_ventas': total_costo_ventas, 'er_utilidad_bruta': utilidad_bruta,
        'er_total_gastos_operativos': total_gastos_operativos, 'er_utilidad_operativa': utilidad_operativa,
        'er_total_gastos_financieros': total_gastos_financieros, 'er_utilidad_antes_impuesto': utilidad_antes_impuesto,
        'er_impuesto': impuesto, 'er_utilidad_neta': utilidad_antes_impuesto - impuesto,
    }
    return render(request, 'estado_resultados.html', context)


def balance_general(request):
    def total_tipo(tipo, deudora=True):
        total = Decimal('0')
        for c in CuentaContable.objects.filter(tipo=tipo):
            td = c.movimientos.filter(tipo='debe').aggregate(t=Sum('monto'))['t'] or Decimal('0')
            th = c.movimientos.filter(tipo='haber').aggregate(t=Sum('monto'))['t'] or Decimal('0')
            total += (td - th) if deudora else (th - td)
        return total
    act, pas, pat = total_tipo('activo'), total_tipo('pasivo', False), total_tipo('patrimonio', False)
    ing, gas = total_tipo('ingreso', False), total_tipo('gasto')
    res = ing - gas
    return render(request, 'balance_general.html', {'total_activos': act, 'total_pasivos': pas, 'total_patrimonio': pat + res, 'resultados_acumulados': res})


def reporte_completo(request):
    if request.method == 'POST':
        empresa, correo = request.POST.get('empresa', 'Mi Empresa'), request.POST.get('correo', '')
        ctx = get_reporte_context(); ctx['empresa'] = empresa
        html = render_to_string('reporte_pdf.html', ctx)
        pdf = BytesIO(); pisa.CreatePDF(BytesIO(html.encode('UTF-8')), dest=pdf)
        if correo:
            email = EmailMessage(subject=f'Reporte - {empresa}', body='Adjunto reporte.', from_email=settings.EMAIL_HOST_USER, to=[correo])
            email.attach('Reporte.pdf', pdf.getvalue(), 'application/pdf'); email.send()
            messages.success(request, 'Enviado con éxito.')
        return redirect('reporte_completo')
    return render(request, 'menu_reporte.html')