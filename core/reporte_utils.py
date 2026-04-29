from decimal import Decimal
from django.db.models import Sum
from .models import CuentaContable, AsientoContable

def get_reporte_context():
    ctx = {}
    # Ordenamos asientos por fecha para el Libro Diario
    ctx['asientos'] = AsientoContable.objects.prefetch_related('movimientos__cuenta').order_by('fecha', 'id')
    
    cuentas = CuentaContable.objects.all()
    bal_comp_datos = []
    mayor_datos = [] # Nueva lista para el Libro Mayor en el PDF
    

    gran_total_debe = Decimal('0')
    gran_total_haber = Decimal('0')
    gran_saldo_deudor = Decimal('0')
    gran_saldo_acreedor = Decimal('0')

    for cuenta in cuentas:
        total_debe = cuenta.movimientos.filter(tipo='debe').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        total_haber = cuenta.movimientos.filter(tipo='haber').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        
        if total_debe == 0 and total_haber == 0:
            continue
            
        # Lógica para Balance de Comprobación
        saldo = total_debe - total_haber
        saldo_deudor = saldo if saldo > 0 else Decimal('0')
        saldo_acreedor = abs(saldo) if saldo < 0 else Decimal('0')
        
        bal_comp_datos.append({
            'cuenta': cuenta, 
            'total_debe': total_debe, 
            'total_haber': total_haber,
            'saldo_deudor': saldo_deudor, 
            'saldo_acreedor': saldo_acreedor
        })
        
        # Acumular totales generales
        gran_total_debe += total_debe
        gran_total_haber += total_haber
        gran_saldo_deudor += saldo_deudor
        gran_saldo_acreedor += saldo_acreedor

        # Lógica para el Libro Mayor (Cronológico)
        movimientos = cuenta.movimientos.select_related('asiento').order_by('asiento__fecha', 'asiento__id')
        # El saldo final del mayor depende de la naturaleza de la cuenta
        saldo_final_mayor = (total_debe - total_haber) if cuenta.naturaleza_deudora else (total_haber - total_debe)
        
        mayor_datos.append({
            'cuenta': cuenta,
            'movimientos': movimientos,
            'total_debe': total_debe,
            'total_haber': total_haber,
            'saldo_final': saldo_final_mayor
        })

    ctx['bal_comp_datos'] = bal_comp_datos
    ctx['mayor_datos'] = mayor_datos
    
    # Pasar totales al contexto del PDF
    ctx['gran_total_debe'] = gran_total_debe
    ctx['gran_total_haber'] = gran_total_haber
    ctx['gran_saldo_deudor'] = gran_saldo_deudor
    ctx['gran_saldo_acreedor'] = gran_saldo_acreedor
    
    # --- Estado de Resultados (Mantenemos tu lógica intacta) ---
    def calcular_saldos_por_tipo(tipo_cuenta, subcat=None, exclude_subcat=None):
        qs = CuentaContable.objects.filter(tipo=tipo_cuenta)
        if subcat: qs = qs.filter(subcategoria=subcat)
        if exclude_subcat: qs = qs.exclude(subcategoria__in=exclude_subcat)
        total = Decimal('0')
        for c in qs:
            t_d = c.movimientos.filter(tipo='debe').aggregate(t=Sum('monto'))['t'] or Decimal('0')
            t_h = c.movimientos.filter(tipo='haber').aggregate(t=Sum('monto'))['t'] or Decimal('0')
            if tipo_cuenta == 'gasto': total += (t_d - t_h)
            else: total += (t_h - t_d)
        return total

    ctx['er_total_ventas'] = calcular_saldos_por_tipo('ingreso', exclude_subcat=['otro_ingreso'])
    ctx['er_total_costo_ventas'] = calcular_saldos_por_tipo('gasto', subcat='costo_ventas')
    ctx['er_utilidad_bruta'] = ctx['er_total_ventas'] - ctx['er_total_costo_ventas']
    ctx['er_total_gastos_operativos'] = calcular_saldos_por_tipo('gasto', exclude_subcat=['costo_ventas', 'gasto_financiero', 'otro_gasto'])
    ctx['er_utilidad_operativa'] = ctx['er_utilidad_bruta'] - ctx['er_total_gastos_operativos']
    ctx['er_total_gastos_financieros'] = calcular_saldos_por_tipo('gasto', subcat='gasto_financiero')
    otros_ing = calcular_saldos_por_tipo('ingreso', subcat='otro_ingreso')
    otros_gas = calcular_saldos_por_tipo('gasto', subcat='otro_gasto')
    ctx['er_utilidad_antes_impuesto'] = ctx['er_utilidad_operativa'] - ctx['er_total_gastos_financieros'] + otros_ing - otros_gas
    ctx['er_impuesto'] = (ctx['er_utilidad_antes_impuesto'] * Decimal('0.30')).quantize(Decimal('0.01')) if ctx['er_utilidad_antes_impuesto'] > 0 else Decimal('0')
    ctx['er_utilidad_neta'] = ctx['er_utilidad_antes_impuesto'] - ctx['er_impuesto']

    return ctx