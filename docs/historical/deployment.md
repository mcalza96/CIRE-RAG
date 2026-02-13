# Guia de Despliegue: Lanzamiento de JIT Data Analytics

Para que tu plataforma sea accesible en todo el mundo, usaremos **Vercel** por su integracion nativa con Next.js.

## 1. Preparacion en GitHub
1. Crea un nuevo repositorio en GitHub (ej: `jit-data-analytics`).
2. Sube tu codigo:
   ```bash
   git add .
   git commit -m "Identity Shift - JIT Data Analytics"
   git push origin main
   ```

## 2. Configuracion en Vercel
1. Ve a [vercel.com](https://vercel.com) e importa tu repositorio.
2. En la seccion **Environment Variables**, debes copiar exactamente los valores de tu `.env.local`:

| Variable | Descripcion |
| :--- | :--- |
| `NEXT_PUBLIC_SUPABASE_URL` | La URL de tu proyecto en Supabase. |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | La llave publica anonima de Supabase. |
| `SUPABASE_SERVICE_ROLE_KEY` | Para acciones administrativas de alta seguridad. |
| `ADMIN_EMAIL` | El email que tendra acceso al panel `/admin` (ej: `mca@test.com`). |

3. Haz clic en **Deploy**. Vercel generara una URL publica para ti.

## 3. Configuracion en Supabase (Crucial)
Para que el login funcione en produccion, debes anadir la URL de Vercel a la lista blanca de Supabase:
1. Ve a **Authentication > URL Configuration**.
2. Anade la URL que te dio Vercel (ej: `https://tu-proyecto.vercel.app`) en **Site URL** y **Redirect URLs**.

---

## Checklist de Lanzamiento
- [ ] Estan las tablas SQL creadas en Supabase?
- [ ] Estan configuradas las politicas RLS para JIT Data?
- [ ] Has configurado el `ADMIN_EMAIL` correcto?
- [ ] Has probado el flujo de Magic Link en el dominio de produccion (`jitdata.cl`)?

Felicidades por lanzar la plataforma de JIT Data Analytics.
