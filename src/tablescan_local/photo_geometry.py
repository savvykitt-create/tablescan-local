"""Reproducible photograph geometry. Original files are never modified."""
from __future__ import annotations

import copy
import cv2
import numpy as np

from .i18n import tr


def validate_transform(transform):
    if not isinstance(transform, dict) or transform.get('version') != 1:
        raise ValueError(tr('Invalid photograph alignment. Align the four corners again.'))
    for key in ('corners', 'target'):
        points = np.asarray(transform.get(key), dtype=float)
        if (points.shape != (4, 2) or not np.isfinite(points).all()
                or np.any(points < 0) or np.any(points > 1)
                or not cv2.isContourConvex(np.float32(points))
                or cv2.contourArea(np.float32(points), oriented=True) < .01):
            raise ValueError(tr('The four corners must form a non-crossing table boundary.'))
    # Only bounded, monotonic displacement meshes produced by refine_curvature.
    mesh = transform.get('mesh')
    if mesh is not None:
        x, y = np.asarray(mesh.get('x'), float), np.asarray(mesh.get('y'), float)
        sx, sy = np.asarray(mesh.get('source_x'), float), np.asarray(mesh.get('source_y'), float)
        if (x.ndim != 1 or y.ndim != 1 or not 2 <= len(x) <= 102 or not 2 <= len(y) <= 202
                or sx.shape != (len(y), len(x)) or sy.shape != sx.shape
                or not all(np.isfinite(a).all() for a in (x, y, sx, sy))
                or x[0] != 0 or x[-1] != 1 or y[0] != 0 or y[-1] != 1
                or np.any(np.diff(x) <= 0) or np.any(np.diff(y) <= 0)
                or np.any(np.diff(sx, axis=1) <= 0) or np.any(np.diff(sy, axis=0) <= 0)
                or np.max(np.abs(sx-x[None,:])) > .025
                or np.max(np.abs(sy-y[:,None])) > .025):
            raise ValueError(tr('Invalid photograph alignment. Align the four corners again.'))


def corner_transform(corners, shape):
    """Normalized TL, TR, BR, BL; preserve the surrounding header and page."""
    p = np.asarray(corners, float)
    if p.shape != (4, 2):
        raise ValueError(tr('Mark all four table corners.'))
    # Preserve photographed scale and placement; no arbitrary A4 assumption.
    left, right = float((p[0,0]+p[3,0])/2), float((p[1,0]+p[2,0])/2)
    top, bottom = float((p[0,1]+p[1,1])/2), float((p[2,1]+p[3,1])/2)
    result = dict(version=1, corners=p.tolist(), target=[[left,top],[right,top],[right,bottom],[left,bottom]])
    validate_transform(result)
    return result


def apply_transform(image, transform):
    if not transform:
        return image
    validate_transform(transform)
    h,w = image.shape[:2]
    matrix = cv2.getPerspectiveTransform(
        np.float32(np.asarray(transform['corners'])*[w,h]), np.float32(np.asarray(transform['target'])*[w,h]))
    result = cv2.warpPerspective(image, matrix, (w,h), flags=cv2.INTER_CUBIC, borderValue=(255,255,255))
    mesh = transform.get('mesh')
    if mesh:
        # Separable interpolation of a small control mesh, bounded by image size.
        xs,ys = np.asarray(mesh['x']),np.asarray(mesh['y'])
        horizontal = np.asarray([np.interp(np.arange(w)/w,xs,row) for row in mesh['source_x']])
        vertical = np.asarray([np.interp(np.arange(h)/h,ys,col) for col in np.asarray(mesh['source_y']).T])
        # Interpolate across the other dimension in strips to bound temporaries.
        corrected = np.empty_like(result)
        for start in range(0,h,128):
            yy = np.arange(start,min(h,start+128))/h
            ix = np.clip(np.searchsorted(ys,yy)-1,0,len(ys)-2)
            weight = ((yy-ys[ix])/(ys[ix+1]-ys[ix]))[:,None]
            mapx = ((1-weight)*horizontal[ix]+weight*horizontal[ix+1])*w
            mapy = np.asarray([np.interp(np.arange(w)/w,xs,vertical[:,j]) for j in range(start,min(h,start+128))])*h
            corrected[start:start+len(yy)] = cv2.remap(result,np.float32(mapx),np.float32(mapy),cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT,borderValue=(255,255,255))
        result = corrected
    return result


def _line_groups(lines, horizontal, center, gap=5):
    items=[]
    for x1,y1,x2,y2 in lines:
        a,b,c,d = (x1,y1,x2,y2) if horizontal else (y1,x1,y2,x2)
        if abs(c-a) < 1:
            continue
        slope=(d-b)/(c-a)
        if abs(slope) > .35:
            continue
        intercept=b-slope*a
        items.append((slope*center+intercept,abs(c-a),slope,intercept,min(a,c),max(a,c)))
    groups=[]
    for item in sorted(items):
        if not groups or item[0]-np.mean([r[0] for r in groups[-1]]) > gap:
            groups.append([item])
        else:
            groups[-1].append(item)
    return [max(group,key=lambda r:r[1]) for group in groups]


def find_table_corners(image):
    """Find a repeated ruled lattice, excluding page edges and short handwriting."""
    h,w=image.shape[:2]
    ratio=min(1.,1600/max(h,w))
    small=cv2.resize(image,None,fx=ratio,fy=ratio,interpolation=cv2.INTER_AREA)
    h,w=small.shape[:2]
    gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
    binary=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,41,11)
    lines=cv2.HoughLinesP(binary,1,np.pi/1800,max(50,round(w*.05)),minLineLength=max(60,min(w,h)*.23),maxLineGap=20)
    if lines is None:
        return None
    lines=lines.reshape(-1,4)
    horizontal=_line_groups([l for l in lines if abs(l[2]-l[0]) > w*.4],True,w/2)
    if len(horizontal)<6:
        return None
    values=np.array([r[0] for r in horizontal])
    # A coherent repeated sequence must explain at least six observed rules.
    best=[]
    for step in np.diff(values):
        if not max(8,h*.009) <= step <= h*.15:
            continue
        for start in range(len(values)):
            group=[start]
            for i in range(start+1,len(values)):
                delta=(values[i]-values[group[-1]])/step
                if .68 <= delta <= 1.35:
                    group.append(i)
                elif delta>1.35:
                    break
            if len(group)>len(best):
                best=group
    if len(best)<6:
        return None
    rows=[horizontal[i] for i in best]
    low,high=rows[0][0],rows[-1][0]
    if high-low<h*.18:
        return None
    left=float(np.percentile([r[4] for r in rows],15))
    right=float(np.percentile([r[5] for r in rows],85))
    vertical=_line_groups([l for l in lines if abs(l[3]-l[1])>(high-low)*.42],False,(low+high)/2)
    vertical=[r for r in vertical if left-w*.015 <= r[0] <= right+w*.015
              and min(r[5],high)-max(r[4],low)>(high-low)*.42]
    if len(vertical)<3:
        return None
    def intersection(row,col):
        yr,br=row[2:4]; xc,bc=col[2:4]
        x=(xc*br+bc)/(1-xc*yr)
        return [x/w,(yr*x+br)/h]
    corners=[intersection(rows[0],vertical[0]),intersection(rows[0],vertical[-1]),
             intersection(rows[-1],vertical[-1]),intersection(rows[-1],vertical[0])]
    try:
        corner_transform(corners,image.shape)
    except ValueError:
        return None
    return corners


def refine_curvature(image, detection, transform):
    """Follow small residual rule bends in local strips; reject unsupported warps."""
    h,w=image.shape[:2]
    rows=np.array(detection.row_guides); cols=np.array(detection.column_guides)
    if len(rows)<5 or len(cols)<4 or detection.warnings:
        return transform
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    mask=cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,41,11)
    # Neutral printed rules; saturated annotations do not steer the mesh.
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    mask=cv2.bitwise_and(mask,cv2.inRange(hsv,np.array([0,0,0]),np.array([179,115,225])))
    xs=np.linspace(max(.001,cols[0]),min(.999,cols[-1]),17)
    ys=np.r_[0,rows,1]
    xs=np.r_[0,xs,1]
    sx=np.tile(xs,(len(ys),1)); sy=np.tile(ys[:,None],(1,len(xs)))
    radius=max(2,min(14,round(float(np.median(np.diff(rows)))*h*.22)))
    half=max(12,round((cols[-1]-cols[0])*w/40))
    evidence=0; measured=0
    for i,y in enumerate(rows,1):
        cy=round(y*h)
        for j,x in enumerate(xs[1:-1],1):
            cx=round(x*w); a,b=max(0,cy-radius),min(h,cy+radius+1)
            strip=mask[a:b,max(round(cols[0]*w)+2,cx-half):min(round(cols[-1]*w)-2,cx+half)]
            if strip.shape[1]<10:
                continue
            projection=np.mean(strip>0,axis=1); peak=int(np.argmax(projection)); measured+=1
            if projection[peak]>=.60:
                near=np.flatnonzero(projection>=max(.6,float(projection[peak])*.8))
                sy[i,j]=(a+float(np.median(near)))/h; evidence+=1
        # Extend observed bend into adjacent margins smoothly, not into other rows.
    if evidence < measured*.75:
        return transform
    # Follow each vertical rule at multiple heights and interpolate its displacement.
    vx=np.r_[0,cols,1]
    observed=np.tile(vx,(len(ys),1))
    radx=max(2,min(14,round(float(np.median(np.diff(cols)))*w*.15)))
    for i,y in enumerate(ys[1:-1],1):
        cy=round(y*h); hh=max(12,round(float(np.median(np.diff(rows)))*h*.35))
        for j,x in enumerate(cols,1):
            cx=round(x*w); a,b=max(0,cx-radx),min(w,cx+radx+1)
            strip=mask[max(0,cy-hh):min(h,cy+hh),a:b]
            projection=np.mean(strip>0,axis=0)
            if projection.size and projection.max()>=.65:
                near=np.flatnonzero(projection>=max(.65,float(projection.max())*.8))
                observed[i,j]=(a+float(np.median(near)))/w
        sx[i]=np.interp(xs,vx,observed[i])
    if max(np.max(np.abs(sx-xs)),np.max(np.abs(sy-ys[:,None])))<.0004:
        return transform
    result=copy.deepcopy(transform)
    result['mesh']=dict(x=xs.tolist(),y=ys.tolist(),source_x=sx.tolist(),source_y=sy.tolist())
    try:
        validate_transform(result)
    except ValueError:
        return transform
    return result


def prepare_photo(image, *, corners=None):
    """Return transformed pixels, serializable geometry and measured grid."""
    from .imaging import detect_grid
    manual = corners is not None
    try:
        baseline = detect_grid(image)
    except ValueError:
        baseline = None
    if corners is None:
        corners=find_table_corners(image)
    if corners is None:
        return image, {}, detect_grid(image)
    transform=corner_transform(corners,image.shape)
    # Leave essentially straight scans bit-for-bit unchanged.
    difference=np.max(np.abs(np.asarray(transform['corners'])-np.asarray(transform['target'])))
    if difference<.002 and not manual:
        try:
            d=detect_grid(image)
            if len(d.row_guides)>5 and len(d.column_guides)>3 and not d.warnings:
                return image,{},d
        except ValueError:
            pass
    warped=apply_transform(image,transform)
    detection=detect_grid(warped)
    refined=refine_curvature(warped,detection,transform)
    if refined!=transform:
        candidate=apply_transform(image,refined)
        try:
            d=detect_grid(candidate)
            if (len(d.row_guides),len(d.column_guides))==(len(detection.row_guides),len(detection.column_guides)) and not d.warnings:
                warped,transform,detection=candidate,refined,d
        except ValueError:
            pass
    if not manual and baseline is not None and not baseline.warnings:
        if (detection.warnings or len(detection.row_guides)<len(baseline.row_guides)
                or len(detection.column_guides)<len(baseline.column_guides)):
            return image,{},baseline
    return warped,transform,detection
